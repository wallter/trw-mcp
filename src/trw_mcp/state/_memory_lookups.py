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
    checkpointed = int(row[2]) if row and row[2] is not None else 0
    return CheckpointResult(busy=busy, checkpointed=checkpointed, mode="PASSIVE")


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
      the checkpoint necessary. ``TRUNCATE`` is requested only when this process
      is the sole live writer, which trw-memory re-proves with a bounded
      ``BEGIN EXCLUSIVE`` probe before it resets anything.

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
        mode = result["mode"].lower()
        # A resetting checkpoint that came back as PASSIVE was downgraded — the
        # backend either fell back on busy=1 readers or the engine is unsafe.
        # The bare PASSIVE path requested PASSIVE deliberately, so it is not a
        # busy fallback (FR03: truncate_busy means a TRUNCATE attempt yielded
        # PASSIVE).
        truncate_busy = requested_truncate and mode == "passive"
        if truncate_busy:
            logger.info(
                "wal_checkpoint_truncate_busy",
                detail="readers held pages; fell back to PASSIVE",
            )
        wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
        wal_size_after_mb = round(wal_size_after / (1024 * 1024), 1)
        # Two clocks, because they answer different questions (review finding 4).
        # ATTEMPT drives the age trigger: a busy=1 checkpoint still RAN, so it
        # counts (US-001 AC3), and advancing it stops the trigger hot-looping on
        # a store whose readers never release. EFFECTIVE drives the doctor's
        # WARN: it advances only when the checkpoint actually accomplished
        # something — frames written back, or the file shrank. Without the
        # split, a store where PASSIVE runs hourly and reclaims nothing (the
        # unsafe-engine steady state) would report a fresh checkpoint age
        # forever and the row could never warn. The error path below advances
        # NEITHER, so the age trigger retries next sweep (NFR02).
        # The marker writes are fail-open, but NOT invisible: a checkpoint whose
        # attempt clock never landed leaves the age unknown, so the age trigger
        # is due again immediately and the hot-loop protection this pair exists
        # for is not in force. Reporting an unqualified success there asserted a
        # protection that had not been established.
        attempt_recorded = record_checkpoint_attempt(db_path)
        effective_due = checkpointed > 0 or wal_size_after < trigger.wal_size_bytes
        effective_recorded = record_effective_checkpoint(db_path) if effective_due else True
        markers_persisted = attempt_recorded and effective_recorded
        logger.info(
            "wal_checkpoint_complete",
            mode=mode,
            trigger=trigger.reason,
            wal_size_before_mb=wal_size_mb,
            wal_size_after_mb=wal_size_after_mb,
            pages_checkpointed=checkpointed,
            busy=busy,
            truncate_busy=truncate_busy,
            markers_persisted=markers_persisted,
        )
        result_dict: WalCheckpointResultDict = {
            "checkpointed": True,
            "mode": mode,
            "wal_size_before_mb": wal_size_mb,
            "wal_size_after_mb": wal_size_after_mb,
            "pages_checkpointed": checkpointed,
            "busy": busy,
            "truncate_busy": truncate_busy,
            "markers_persisted": markers_persisted,
        }
        if not markers_persisted:
            result_dict["reason"] = "checkpoint_marker_write_failed"
            result_dict["advisory"] = (
                "the WAL was checkpointed but its timestamp marker could not be written, "
                "so checkpoint age is unknown and the age trigger will fire again next evaluation"
            )
            _warn("wal_checkpoint_marker_not_persisted", db_path=str(db_path))
        return result_dict
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        _warn("wal_checkpoint_failed", exc_info=True)
        return {"error": True, "reason": "checkpoint_failed"}
