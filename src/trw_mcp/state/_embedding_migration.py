"""Automatic, bounded re-embedding of stored vectors outside the active space.

Dense recall and similarity edges admit only vectors recorded in the loaded
embedder's space (``_embedding_space``). A store written by an older model, or
before vectors recorded any space, therefore has no dense recall at all after an
upgrade until it is re-embedded. The explicit command
(``trw-mcp update-project --repair-embeddings N``) does that one page at a time;
this module runs the SAME page operation (``_embedding_repair.repair_page``)
automatically, so an upgrade migrates itself:

- **Bounded.** One run stops at a wall-clock budget or a row budget, whichever
  comes first. Each row is re-checked and written in its own short transaction
  and no lock is held during inference, so a run can stop anywhere.
- **Resumable.** The page cursor and the counts are persisted after every page
  in ``.trw/memory/embedding-migration.json``; the next run continues there.
- **Idempotent.** ``repair_page`` skips a vector whose recorded provenance
  already matches the active space and the entry's current text, so repeating
  a page costs a read, never a second encode.
- **Cross-process safe.** A non-blocking exclusive ``flock`` on
  ``.trw/memory/embedding-migration.lock`` admits one migrating process per
  project; every other caller gets ``run=busy`` and does nothing.
- **Terminating.** A full pass with no failures or concurrent changes completes
  the migration. Concurrent changes schedule another budget-bounded pass. A
  pass that repaired nothing but still failed rows marks it ``stalled`` instead
  of retrying forever; a later run restarts if the stale count grows.

The state is keyed on the active space's full identity, so a later model change
restarts it. On completion the deliver-time graph sweep is restarted
(``_graph_backfill.restart_graph_sweep``) so similarity edges are rebuilt from
the re-embedded vectors; that sweep is already deadline-bounded and resumable.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from trw_memory.embeddings._space_gate import active_embedding_space, select_space_vectors
from trw_memory.embeddings.interface import EmbeddingProvider
from trw_memory.embeddings.provenance import EmbeddingSpace, provider_embedding_space
from trw_memory.storage.interface import EntryCursor

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.state._constants import DEFAULT_NAMESPACE

__all__ = [
    "Budget",
    "MigrationState",
    "load_migration_state",
    "migration_lock_path",
    "migration_state_path",
    "run_migration",
    "space_key",
]

logger = structlog.get_logger(__name__)

_STATE_FILENAME = "embedding-migration.json"
_LOCK_FILENAME = "embedding-migration.lock"
_STATE_VERSION = 1

#: Rows handed to one ``repair_page`` call: the cursor is persisted after each,
#: so an interrupted run repeats at most one page (skipped, not re-encoded).
PAGE_SIZE = 100

#: Vector records read per chunk while counting stale vectors.
_MEASURE_CHUNK = 500

#: Terminal states: nothing left that this migration can re-embed.
_DONE = frozenset({"complete", "stalled"})


@dataclass
class MigrationState:
    """Persisted progress of one store's migration into one embedding space."""

    space: str = ""
    model: str = ""
    revision: str = ""
    dim: int = 0
    status: str = "pending"
    total: int = 0
    repaired: int = 0
    failed: int = 0
    residual: int = 0
    residual_unqualified: int = 0
    pass_repaired: int = 0
    pass_failed: int = 0
    pass_changed: int = 0
    cursor: dict[str, str] | None = None
    updated_at: str = ""

    @property
    def done(self) -> bool:
        return self.status in _DONE


@dataclass(frozen=True)
class Budget:
    """How much one run may do: wall-clock seconds, inspected rows, page size."""

    seconds: float
    rows: int
    page_size: int = PAGE_SIZE
    clock: Callable[[], float] = time.monotonic


def space_key(space: EmbeddingSpace) -> str:
    """The identity a migration targets: every field of the space descriptor."""
    return f"{space.encoding}|{space.artifact_sha256}|{space.dimensions}"


def migration_state_path(trw_dir: Path) -> Path:
    return trw_dir / "memory" / _STATE_FILENAME


def migration_lock_path(trw_dir: Path) -> Path:
    return trw_dir / "memory" / _LOCK_FILENAME


def load_migration_state(trw_dir: Path) -> MigrationState | None:
    """The persisted state, or ``None`` when absent, unreadable or foreign."""
    try:
        raw = json.loads(migration_state_path(trw_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: absent/unreadable state = "not started"; idempotent
        return None
    if not isinstance(raw, dict) or raw.get("version") != _STATE_VERSION:
        return None
    known = set(MigrationState.__dataclass_fields__)
    try:
        return MigrationState(**{key: value for key, value in raw.items() if key in known})
    except TypeError:  # trw-fail-silent-allow: a malformed state restarts the migration, which is idempotent
        return None


def _save_state(trw_dir: Path, state: MigrationState) -> None:
    state.updated_at = datetime.now(timezone.utc).isoformat()
    path = migration_state_path(trw_dir)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({"version": _STATE_VERSION, **asdict(state)}, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Losing progress costs a re-read of already-migrated pages (skipped
        # without encoding), never a wrong vector.
        logger.warning("embedding_migration_state_write_failed", path=str(path), exc_info=True)


def _measure(backend: Any, provider: object) -> tuple[int, int] | None:
    """``(excluded, unqualified)`` stored vectors outside the provider's space.

    Counted a chunk at a time so a large store is never held in memory at once
    (``stale_space_selection`` materialises every admitted vector).
    """
    space = active_embedding_space(provider)
    reader = getattr(backend, "get_vector_records", None)
    if space is None or not callable(reader):
        return None
    ids = sorted(backend.existing_vector_ids(namespace=DEFAULT_NAMESPACE))
    excluded = unqualified = 0
    for start in range(0, len(ids), _MEASURE_CHUNK):
        selection = select_space_vectors(
            reader(ids[start : start + _MEASURE_CHUNK], namespace=DEFAULT_NAMESPACE), space
        )
        excluded += selection.excluded
        unqualified += selection.unqualified
    return excluded, unqualified


def _missing(backend: Any) -> int:
    """Entries with no vector at all: a pass encodes those too (``repair_page``)."""
    entries = int(backend.count(namespace=DEFAULT_NAMESPACE))
    return max(0, entries - len(backend.existing_vector_ids(DEFAULT_NAMESPACE)))


def _begin(
    state: MigrationState | None, key: str, measured: tuple[int, int], identity: dict[str, Any], missing: int
) -> MigrationState:
    """Start (or restart) a migration toward *key* given the measured stale count.

    ``total`` is the pass's expected work: stale vectors plus entries with no
    vector, which the same pass encodes. It is an estimate (canary or blank
    entries are counted but skipped), used only as a progress denominator.
    """
    stale, unqualified = measured
    if state is None or state.space != key:
        state = MigrationState(space=key, total=stale + missing, **identity)
    else:  # stale vectors appeared after a finished pass
        state.total = state.repaired + stale + missing
    state.status = "complete" if stale == 0 else "in_progress"
    state.residual, state.residual_unqualified = (stale, unqualified) if stale == 0 else (0, 0)
    state.cursor, state.pass_repaired, state.pass_failed, state.pass_changed = None, 0, 0, 0
    return state


def _finish_pass(state: MigrationState, measured: tuple[int, int] | None, trw_dir: Path) -> bool:
    """Settle a completed pass; return True when another pass should run."""
    remaining, unqualified = measured if measured is not None else (0, 0)
    if state.pass_changed:
        # Canonical rows raced provider I/O: retry, unlike orphan/canary residuals.
        state.pass_repaired, state.pass_failed, state.pass_changed = 0, 0, 0
        return True
    if state.pass_failed == 0 or remaining == 0:
        state.status = "complete"
    elif state.pass_repaired == 0:
        state.status = "stalled"
    else:
        state.pass_repaired, state.pass_failed = 0, 0
        return True
    # What a clean pass leaves behind belongs to no live entry (orphaned or
    # canary vectors); recorded so it is not mistaken for new work later.
    state.residual, state.residual_unqualified = remaining, unqualified
    if state.repaired:
        from trw_mcp.state._graph_backfill import restart_graph_sweep

        restart_graph_sweep(trw_dir)
    logger.info(
        "embedding_migration_finished",
        status=state.status,
        repaired=state.repaired,
        total=state.total,
        residual=remaining,
        model=state.model,
    )
    return False


def _report(state: MigrationState, run: str, **extra: object) -> dict[str, object]:
    report: dict[str, object] = {
        "status": state.status,
        "run": run,
        "repaired": state.repaired,
        "total": state.total,
        "failed": state.failed,
        "residual": state.residual,
        "model": state.model,
    }
    report.update(extra)
    return report


def run_migration(
    trw_dir: Path, provider: EmbeddingProvider, *, identity: dict[str, Any], budget: Budget
) -> dict[str, object]:
    """Re-embed stale vectors of *trw_dir*'s store within one bounded run.

    *identity* is ``{"model", "revision", "dim"}`` of the configured encoder,
    recorded so a session can tell -- without loading the model -- whether a
    finished migration still describes the configured one. Raises what
    ``validated_memory_db`` raises for a store that must not be repaired; every
    other outcome is a report whose ``run`` is ``blocked | busy | idle | worked``.
    """
    from trw_mcp.state._memory_repair import validated_memory_db

    space = provider_embedding_space(provider)
    if space is None:
        return {"status": "blocked", "run": "blocked", "reason": "provider_identity_unavailable"}
    db_path = validated_memory_db(trw_dir)
    fd = os.open(migration_lock_path(trw_dir), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            _lock_ex_nb(fd)
        except OSError:
            # trw:intentional EWOULDBLOCK means another process is migrating this store: report it, do nothing
            return _report(load_migration_state(trw_dir) or MigrationState(**identity), "busy")
        try:
            return _run_locked(trw_dir, provider, db_path, space, identity, budget)
        finally:
            _lock_un(fd)
    finally:
        os.close(fd)


def _run_locked(
    trw_dir: Path,
    provider: EmbeddingProvider,
    db_path: Path,
    space: EmbeddingSpace,
    identity: dict[str, Any],
    budget: Budget,
) -> dict[str, object]:
    from trw_mcp.state._embedding_repair import repair_page
    from trw_mcp.state._memory_repair import open_vector_backend

    start = budget.clock()
    key = space_key(space)
    state = load_migration_state(trw_dir)
    backend = open_vector_backend(db_path, space.dimensions)
    try:
        if state is None or state.space != key or state.done:
            measured = _measure(backend, provider)
            if measured is None:
                return {"status": "blocked", "run": "blocked", "reason": "vector_records_unreadable"}
            if state is not None and state.space == key and state.done and measured[0] <= state.residual:
                return _report(state, "idle")
            state = _begin(state, key, measured, identity, _missing(backend) if measured[0] else 0)
            _save_state(trw_dir, state)
            if state.done:
                return _report(state, "idle")
        inspected = repaired_now = 0
        while budget.clock() - start < budget.seconds and inspected < budget.rows:
            after = EntryCursor(**state.cursor) if state.cursor else None
            size = min(budget.page_size, budget.rows - inspected)
            page = repair_page(backend, provider, max_entries=size, after=after)
            if page["status"] == "blocked":
                return _report(state, "blocked", reason=page.get("reason", ""))
            inspected += page["inspected"]
            repaired_now += page["repaired"]
            state.repaired += page["repaired"]
            state.pass_repaired += page["repaired"]
            state.failed += page["failed"]
            state.pass_failed += page["failed"]
            state.pass_changed += page["changed"]
            state.cursor = page["next_cursor"]
            finished = state.cursor is None and not _finish_pass(state, _measure(backend, provider), trw_dir)
            _save_state(trw_dir, state)
            if finished:
                break
        logger.info(
            "embedding_migration_run",
            status=state.status,
            inspected=inspected,
            repaired=repaired_now,
            progress=f"{state.repaired}/{state.total}",
            elapsed_s=round(budget.clock() - start, 3),
        )
        return _report(state, "worked", inspected=inspected, repaired_this_run=repaired_now)
    finally:
        backend.close()
