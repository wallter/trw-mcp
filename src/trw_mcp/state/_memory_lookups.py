"""Memory adapter — lookup, list, count, access tracking, WAL checkpoint helpers.

Belongs to the ``memory_adapter.py`` facade. Re-exported there for back-compat.

Eight read-side / maintenance helpers that wrap the trw-memory backend.

Extracted as DIST-243 batch 43 to keep the parent ``memory_adapter.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import structlog
from trw_memory.exceptions import StorageError
from trw_memory.models.memory import MemoryStatus
from trw_memory.storage import CheckpointResult

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import WalCheckpointResultDict
from trw_mcp.state._backend_id_lookup import resolve_entry_in_backend
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE
from trw_mcp.state._memory_transforms import _memory_to_learning_dict


def get_backend(trw_dir: Path) -> Any:
    """Resolve get_backend through memory_adapter so test monkeypatches stick."""
    from trw_mcp.state import memory_adapter

    return memory_adapter.get_backend(trw_dir)


def _warn(event: str, **kwargs: Any) -> None:
    """Route warning logs through memory_adapter.logger so test patches stick."""
    from trw_mcp.state import memory_adapter

    memory_adapter.logger.warning(event, **kwargs)


logger = structlog.get_logger(__name__)

_NAMESPACE = DEFAULT_NAMESPACE
_LEARNING_ID_RE = re.compile(r"^L-[0-9a-zA-Z]{4,}$")


def find_entry_by_id(trw_dir: Path, learning_id: str) -> dict[str, object] | None:
    """Look up a single learning entry by ID.

    The element is a ``LearningEntryDict`` (the recall-layer contract);
    widened to ``dict[str, object]`` at the boundary to match the existing
    public signature consumed across scoring/tools.
    """
    backend = get_backend(trw_dir)
    # namespace= is required since the schema-5 namespace boundary; _NAMESPACE
    # is the same constant every sibling list_entries call in this module uses.
    entry = backend.get(learning_id, namespace=_NAMESPACE)
    return cast("dict[str, object]", _memory_to_learning_dict(entry)) if entry is not None else None


def list_active_learnings(
    trw_dir: Path,
    *,
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, object]]:
    """List active entries used by claude_md.py for promotion + analytics."""
    backend = get_backend(trw_dir)
    entries = backend.list_entries(status=MemoryStatus.ACTIVE, namespace=_NAMESPACE, limit=limit)
    return [
        cast("dict[str, object]", _memory_to_learning_dict(entry))
        for entry in entries
        if entry.importance >= min_impact and entry.metadata.get("system_canary") != "true"
    ]


def list_entries_by_status(
    trw_dir: Path,
    *,
    status: str = "active",
    min_impact: float = 0.0,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, object]]:
    """Bulk listing by status (PRD-FIX-033 FR01 — single SQLite query)."""
    try:
        mem_status = MemoryStatus(status)
    except ValueError:
        return []
    backend = get_backend(trw_dir)
    entries = backend.list_entries(status=mem_status, namespace=_NAMESPACE, limit=limit)
    return [
        cast("dict[str, object]", _memory_to_learning_dict(entry))
        for entry in entries
        if entry.importance >= min_impact and entry.metadata.get("system_canary") != "true"
    ]


def find_yaml_path_for_entry(trw_dir: Path, entry_id: str) -> Path | None:
    """Resolve YAML file path for an entry_id (PRD-FIX-033 FR05)."""
    cfg = get_config()
    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        return None
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
    candidate = entries_dir / f"{sanitized}.yaml"
    if candidate.exists():
        return candidate
    for yaml_file in entries_dir.glob("*.yaml"):
        if yaml_file.name == "index.yaml":
            continue
        if sanitized in yaml_file.stem or entry_id in yaml_file.stem:
            return yaml_file
    return None


def count_entries(trw_dir: Path) -> int:
    """Return total entry count (excluding system canaries)."""
    backend = get_backend(trw_dir)
    return len(
        [
            entry
            for entry in backend.list_entries(namespace=_NAMESPACE, limit=100_000)
            if entry.metadata.get("system_canary") != "true"
        ]
    )


def _increment_backend_access(backend: Any, learning_ids: list[str], now: datetime) -> None:
    """Best-effort access tracking against one owning backend."""
    if not learning_ids:
        return

    increment_recall_access = getattr(backend, "increment_recall_access", None)
    if callable(increment_recall_access):
        try:
            increment_recall_access(learning_ids, accessed_at=now)
            return
        except (StorageError, OSError, RuntimeError, sqlite3.Error, ValueError, TypeError):
            _warn("access_tracking_batch_update_failed", exc_info=True, entry_ids=learning_ids)

    # PRD-CORE-245 FR03: the per-entry fallback resolves the row through the
    # namespace-aware helper and then qualifies the write with the namespace it
    # found. Reading by bare id here used to raise a TypeError that the broad
    # ``except`` swallowed, so the fallback silently tracked nothing.
    for lid in learning_ids:
        try:
            entry = resolve_entry_in_backend(backend, lid)
            if entry is not None:
                backend.update(
                    lid,
                    namespace=entry.namespace,
                    access_count=entry.access_count + 1,
                    recall_count=entry.recall_count + 1,
                    last_accessed_at=now,
                )
        except Exception:  # per-item: telemetry must not break recall
            _warn("access_tracking_update_failed", exc_info=True, entry_id=lid)


def update_access_tracking(trw_dir: Path, learning_ids: list[str], *, federated: bool = False) -> None:
    """Increment access_count, recall_count, and last_accessed_at for recalled entries.

    PRD-FIX-104-FR01: calls increment_recall_access (not increment_access_counts)
    so that both access_count AND recall_count are incremented in a single batch
    UPDATE, enabling feedback_decay_score in trw-memory lifecycle scoring.
    PRD-FIX-104-FR02: per-entry fallback also increments recall_count.
    """
    unique_ids = list(dict.fromkeys(lid for lid in learning_ids if lid))
    if not unique_ids:
        return
    now = datetime.now(timezone.utc)
    project_backend = get_backend(trw_dir)
    if not federated:
        _increment_backend_access(project_backend, unique_ids, now)
        return

    # Federated recalls can contain project-, user-, and external-store IDs.
    # Resolve ownership before incrementing so user hits are not silently sent
    # to the project DB and duplicate IDs are never counted in both stores.
    project_ids: list[str] = []
    unresolved_ids: list[str] = []
    for lid in unique_ids:
        try:
            (project_ids if resolve_entry_in_backend(project_backend, lid) is not None else unresolved_ids).append(lid)
        except Exception:  # per-item: ownership telemetry must not break recall
            _warn("access_tracking_owner_lookup_failed", exc_info=True, entry_id=lid, tier="project")
            unresolved_ids.append(lid)
    _increment_backend_access(project_backend, project_ids, now)

    from trw_mcp.state._user_tier import peek_user_backend

    user_backend = peek_user_backend()
    if user_backend is None:
        return
    user_ids: list[str] = []
    for lid in unresolved_ids:
        try:
            if resolve_entry_in_backend(user_backend, lid) is not None:
                user_ids.append(lid)
        except Exception:  # per-item: ownership telemetry must not break recall
            _warn("access_tracking_owner_lookup_failed", exc_info=True, entry_id=lid, tier="user")
    _increment_backend_access(user_backend, user_ids, now)


def increment_session_counts(trw_dir: Path, learning_ids: list[str]) -> None:
    """Increment session_count once for each learning surfaced at session start."""
    backend = get_backend(trw_dir)
    seen_ids: set[str] = set()
    valid_ids: list[str] = []
    for lid in learning_ids:
        if lid in seen_ids:
            continue
        seen_ids.add(lid)
        if _LEARNING_ID_RE.fullmatch(lid) is None:
            _warn("session_count_update_skipped_invalid_id", entry_id=lid)
            continue
        valid_ids.append(lid)
    if not valid_ids:
        return
    try:
        backend.increment_session_counts(valid_ids, updated_at=datetime.now(timezone.utc))
    except (StorageError, OSError, RuntimeError, sqlite3.Error, ValueError, TypeError):
        # Best-effort telemetry only: session start must not fail if tracking cannot be persisted.
        _warn("session_count_update_failed", exc_info=True, entry_ids=valid_ids)


def _same_db_path(a: Path, b: Path) -> bool:
    """True when two paths resolve to the same database file (fail-safe)."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _bare_passive_checkpoint(db_path: Path) -> CheckpointResult:
    """Run a PASSIVE WAL checkpoint on a fresh, short-lived connection.

    Used only when no live backend in THIS process owns *db_path* — but another
    process (e.g. a concurrent MCP client's stdio trw-mcp instance) still might.
    PASSIVE never resets the
    WAL, so it cannot trigger the WAL-reset corruption bug regardless of how
    many other connections/processes hold the database. Returns the same
    :class:`CheckpointResult` contract the owning-backend path returns, so the
    caller assembles its rich result from one shape.
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    finally:
        conn.close()
    busy = int(row[0]) if row else 1
    log_frames = int(row[1]) if row and len(row) > 1 and row[1] is not None else 0
    checkpointed = int(row[2]) if row and row[2] is not None else 0
    return CheckpointResult(busy=busy, checkpointed=checkpointed, log_frames=log_frames, mode="PASSIVE")


def maybe_checkpoint_wal(trw_dir: Path) -> WalCheckpointResultDict:
    """Checkpoint the SQLite WAL when the size OR age trigger is due; fail-open.

    PRD-CORE-248 FR04 replaced the size-only trigger and the writer-pressure
    cancellation with a trigger-and-mode split (see
    :mod:`trw_mcp.state._wal_triggers`):

    - **Trigger**: WAL at or above ``wal_checkpoint_threshold_mb`` OR the last
      successful checkpoint older than ``wal_checkpoint_max_age_seconds``. An
      evaluation where nothing is due costs one ``stat`` and opens no
      connection (NFR01).
    - **Mode**: decided by the live-writer set alone. Two or more live writers
      still checkpoint — in ``PASSIVE``, which never resets the WAL — because
      the concurrency this used to abort on is exactly the condition that makes
      the checkpoint necessary. ``TRUNCATE`` is *requested* only when this
      process is the sole live writer, and trw-memory's ``normalize_mode`` then
      downgrades it to ``PASSIVE`` anyway on any engine below SQLite 3.51.3,
      with no caller escape. Those two gates are the whole defence.

      There is deliberately NO third one. An earlier revision of this docstring
      promised that trw-memory "re-proves [sole-writer status] with a bounded
      ``BEGIN EXCLUSIVE`` probe before it resets anything". That probe was
      implemented and then deleted by PRD-CORE-248 OQ-1 ("CLOSED BY REFUSAL",
      ``aad7bb2e26`` reversing ``eeaeb2e8d7``): SQLite refuses
      ``PRAGMA wal_checkpoint`` inside a transaction, so the probe can prove
      exclusivity at acquisition but must COMMIT before the PRAGMA runs, and a
      connection opened in that gap reproduces exactly the two-connection
      precondition the WAL-reset corruption bug needs. The docstring outlived
      the code. Naming a safety mechanism that does not exist is worse than
      naming none, because it is exactly what a future reader consults before
      deciding a reset is safe here.

    PRD-QUAL-050-FR05 + PRD-FIX-081 (retained): the checkpoint runs on the
    backend's single owning connection when one exists. When no backend owns
    the db in this process, a bare PASSIVE checkpoint runs instead — PASSIVE
    cannot reset the WAL, so it is safe whatever else is writing.

    Returns a :class:`WalCheckpointResultDict`: a skip outcome (``skipped``),
    a success outcome with FR03 telemetry (``checkpointed``/``mode``/sizes), or
    a fail-open error outcome (``error``).
    """
    try:
        from trw_mcp.state._wal_triggers import (
            evaluate_wal_trigger,
            record_checkpoint_attempt,
            record_effective_checkpoint,
            record_reset_checkpoint,
            resolve_wal_paths,
            sole_live_writer,
        )

        config = get_config()
        db_path, wal_path = resolve_wal_paths(trw_dir)
        trigger = evaluate_wal_trigger(trw_dir, config)
        if not trigger.due:
            return {"skipped": True, "reason": trigger.reason}
        wal_size_mb = round(trigger.wal_size_bytes / (1024 * 1024), 1)
        is_sole_writer = sole_live_writer(trw_dir, db_path)
        logger.info(
            "wal_checkpoint_starting",
            wal_size_mb=wal_size_mb,
            threshold_mb=config.wal_checkpoint_threshold_mb,
            trigger=trigger.reason,
            sole_writer=is_sole_writer,
        )
        # Prefer the LIVE backend's single connection. Opening a competing bare
        # connection while the backend writer is active is exactly the
        # two-connection condition that detonates the SQLite WAL-reset
        # corruption bug on engines < 3.51.3 (sqlite.org/wal.html §walresetbug).
        # When no backend owns this db we are the sole in-process accessor, so a
        # bare PASSIVE checkpoint cannot reset the WAL. We never CONSTRUCT a
        # backend here — that would run quick_check/recovery on a maintenance
        # path. ``CheckpointResult.mode`` is uppercase; FR03 wants lowercase
        # event/result modes, so we lowercase it once at the boundary.
        from trw_mcp.state._memory_connection import peek_backend

        backend = peek_backend()
        # FR04 clause 3/4: pressure picks the MODE, never whether we run. Only a
        # certified sole writer may ask for a resetting checkpoint.
        if backend is not None and _same_db_path(backend.db_path, db_path) and is_sole_writer:
            requested_truncate = True
            result: CheckpointResult = backend.checkpoint_wal("TRUNCATE")
        elif backend is not None and _same_db_path(backend.db_path, db_path):
            requested_truncate = False
            result = backend.checkpoint_wal("PASSIVE")
        else:
            requested_truncate = False
            result = _bare_passive_checkpoint(db_path)
        busy = result["busy"]
        checkpointed = result["checkpointed"]
        log_frames = result["log_frames"]
        mode = result["mode"].lower()
        # The backend reports a failed PRAGMA as mode="error" rather than
        # raising. That sentinel has to be handled BEFORE anything downstream,
        # or a failure is laundered into a success: the old code classified
        # "error" as truncate_state="reset" (it is simply not "passive"),
        # advanced the attempt clock, and returned checkpointed=True. An
        # injected SQLite I/O error produced exactly that, and the advanced
        # clock then postponed the retry the failure was supposed to trigger.
        # NFR02 wants a failed checkpoint to leave BOTH clocks alone.
        if mode == "error":
            _warn("wal_checkpoint_backend_error", db_path=str(db_path))
            return {"error": True, "reason": "checkpoint_failed"}
        # Why a reset did not happen, as four distinct states rather than one
        # bool. The old ``truncate_busy = requested_truncate and mode ==
        # "passive"`` collapsed three different situations: it read False on
        # every multi-writer and bare-connection call (where TRUNCATE was never
        # REQUESTED), which a consumer reads as "attempted and not blocked";
        # and when it did fire it could not say whether readers held pages or
        # the engine refused outright. Absence of an attempt is not a clear
        # attempt — see FRAMEWORK-CORE, "absence of a measurement is not a
        # measurement of absence".
        truncate_state = _truncate_state(requested_truncate, mode)
        if truncate_state != "not_attempted" and truncate_state != "reset":
            logger.info("wal_checkpoint_truncate_declined", truncate_state=truncate_state)
        wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
        wal_size_after_mb = round(wal_size_after / (1024 * 1024), 1)
        # Two clocks, because they answer different questions.
        # ATTEMPT drives the age trigger: a busy=1 checkpoint still RAN, so it
        # counts, and advancing it stops the trigger hot-looping on a store
        # whose readers never release. EFFECTIVE drives the doctor's WARN: it
        # advances only when the checkpoint accomplished something. The error
        # path above advances NEITHER, so the age trigger retries next sweep.
        # The marker writes are fail-open, but NOT invisible: a checkpoint whose
        # attempt clock never landed leaves the age unknown, so the age trigger
        # is due again immediately and the hot-loop protection this pair exists
        # for is not in force.
        attempt_recorded = record_checkpoint_attempt(db_path)
        # EFFECTIVE means THE BACKLOG WAS CLEARED, measured in frames.
        #
        # This has now been wrong twice, in opposite directions, and both
        # mistakes were the same mistake: measuring a proxy and reporting the
        # conclusion.
        #   v1: ``checkpointed > 0`` -- frames written back, which PASSIVE does
        #       on every run of a busy store while freeing nothing. The clock
        #       never went stale and the doctor WARN was unreachable.
        #   v2: a file-size decrease. But SQLite normally REUSES a fully
        #       checkpointed WAL's allocation instead of shrinking it
        #       (sqlite.org/wal.html#avoiding_excessively_large_wal_files), so a
        #       perfectly healthy store cleared its whole backlog and still
        #       looked stalled. Reproduced by an independent review: 13.3 MiB
        #       WAL, 3,379 frames checkpointed, allocation reused, WARN on every
        #       evaluation. That traded an unreachable alarm for a nuisance one,
        #       which the doctor's own docstring says trains an operator to
        #       ignore the row.
        # The honest signal was in the PRAGMA row all along and was being
        # discarded: column 1 is the WAL backlog. ``checkpointed >= log_frames``
        # means this checkpoint caught up; a persistent shortfall means it did
        # not. File size is reported separately below, as a disk fact, because
        # that is all it is.
        backlog_cleared = busy == 0 and checkpointed >= log_frames
        reclaimed_bytes = trigger.wal_size_bytes - wal_size_after
        reclaimed = reclaimed_bytes > 0
        effective_recorded = record_effective_checkpoint(db_path) if backlog_cleared else True
        # The RESET clock, and the only observation of reclamation this system
        # makes. Written solely when a resetting checkpoint actually ran, because
        # nothing derivable from frames or bytes substitutes for observing one:
        # the effective clock has been redefined three times and each definition
        # replaced the previous proxy rather than adding this measurement. See
        # CHECKPOINT_RESET_SUFFIX for that history.
        reset_recorded = record_reset_checkpoint(db_path) if truncate_state == "reset" else True
        markers_persisted = attempt_recorded and effective_recorded and reset_recorded
        logger.info(
            "wal_checkpoint_complete",
            mode=mode,
            trigger=trigger.reason,
            wal_size_before_mb=wal_size_mb,
            wal_size_after_mb=wal_size_after_mb,
            pages_checkpointed=checkpointed,
            wal_frames=log_frames,
            backlog_cleared=backlog_cleared,
            reclaimed_mb=round(reclaimed_bytes / (1024 * 1024), 1),
            busy=busy,
            truncate_state=truncate_state,
            markers_persisted=markers_persisted,
        )
        # RESPONSE vs LOG. Every field here is paid on every trw_session_start by
        # every calling agent; the structlog event above is free and already
        # carries the full picture. So the response keeps only what a CALLER
        # acts on -- did this reclaim (reclaimed), is the store keeping up
        # (backlog_cleared), and what should be said about it (advisory) --
        # while the frame counts, the reclaimed byte delta and the four-state
        # truncate classification stay in the log for a maintainer.
        #
        # Measured: carrying all of them cost 76 tokens against this repo's
        # 60-token hot-path budget (test_session_start_step_latency). The
        # project's own rule is to cut the bloat rather than raise the ceiling,
        # and the cut fields are exactly the ones it classifies as diagnostics.
        result_dict: WalCheckpointResultDict = {
            "checkpointed": True,
            "mode": mode,
            "wal_size_before_mb": wal_size_mb,
            "wal_size_after_mb": wal_size_after_mb,
            "pages_checkpointed": checkpointed,
            "backlog_cleared": backlog_cleared,
            "reclaimed": reclaimed,
            "busy": busy,
            "markers_persisted": markers_persisted,
        }
        # Both advisories can be true at once, so they concatenate rather than
        # overwrite: a checkpoint can reclaim nothing AND fail to persist its
        # clock, and dropping either half hides a real condition.
        advisories: list[str] = []
        if not backlog_cleared:
            advisories.append(_backlog_advisory(truncate_state, checkpointed, log_frames, busy=busy))
        if not markers_persisted:
            result_dict["reason"] = "checkpoint_marker_write_failed"
            advisories.append(
                "the WAL was checkpointed but its timestamp marker could not be written, "
                "so checkpoint age is unknown and the age trigger will fire again next evaluation"
            )
            _warn("wal_checkpoint_marker_not_persisted", db_path=str(db_path))
        if advisories:
            result_dict["advisory"] = "; ".join(advisories)
        return result_dict
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        _warn("wal_checkpoint_failed", exc_info=True)
        return {"error": True, "reason": "checkpoint_failed"}


def _truncate_state(requested_truncate: bool, mode: str) -> str:
    """Classify why a resetting checkpoint did or did not happen.

    Four states, because the operator question "why is the WAL not shrinking?"
    has four different answers with four different remedies:

    - ``not_attempted`` — TRUNCATE was never requested. Either peers hold the
      store (so this process is not the sole live writer) or no backend owns
      the db here and the bare PASSIVE path ran. Remedy: none needed; this is
      the designed steady state under concurrency.
    - ``refused_unsafe_engine`` — requested, and ``normalize_mode`` downgraded
      it because the driver predates the SQLite 3.51.3 WAL-reset fix. Remedy:
      upgrade the engine (:data:`WAL_RESET_UNSAFE_REMEDY`).
    - ``busy`` — requested on a safe engine, and readers held pages, so the
      backend fell back to PASSIVE. Remedy: none; it will succeed later.
    - ``reset`` — TRUNCATE actually ran.
    """
    if not requested_truncate:
        return "not_attempted"
    if mode != "passive":
        return "reset"
    from trw_memory.storage._dbapi import is_wal_reset_safe

    return "busy" if is_wal_reset_safe() else "refused_unsafe_engine"


def _backlog_advisory(truncate_state: str, checkpointed: int, log_frames: int, *, busy: int) -> str:
    """Say why a checkpoint that ran did not clear the WAL backlog.

    ``checkpointed: True`` and a non-zero ``pages_checkpointed`` both describe
    the OPERATION; a reader takes them as a claim about the OUTCOME. This fires
    only when frames were genuinely left behind — NOT merely when the file did
    not shrink, because SQLite reuses a fully checkpointed WAL's allocation and
    an advisory on that would fire forever on a healthy store.
    """
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    behind = max(log_frames - checkpointed, 0)
    base = (
        f"this checkpoint left {behind} of {log_frames} WAL frame(s) uncheckpointed"
        if behind
        else "this checkpoint could not run to completion"
    )
    if busy or truncate_state == "busy":
        # BOTH conditions, because they do not coincide. On the sole-writer path
        # a busy TRUNCATE is retried as PASSIVE on the same connection and the
        # retry's busy=0 overwrites the flag, so truncate_state is "busy" while
        # the int reads 0. Guarding on the int alone dropped that case through to
        # a bare "left N frames behind" with no cause at all.
        return f"{base}; readers held pages, so it will be retried"
    if truncate_state == "refused_unsafe_engine":
        return f"{base} -- {WAL_RESET_UNSAFE_REMEDY}"
    if truncate_state == "not_attempted":
        return (
            f"{base}, and a resetting checkpoint was not attempted because this "
            "process is not the sole live writer; a clean shutdown of every "
            "server holding the store also lets SQLite remove the WAL"
        )
    return base
