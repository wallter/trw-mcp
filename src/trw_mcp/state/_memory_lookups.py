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
from typing import Any

import structlog
from trw_memory.exceptions import StorageError
from trw_memory.models.memory import MemoryStatus
from trw_memory.storage import CheckpointResult

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import WalCheckpointResultDict
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
    """Look up a single learning entry by ID."""
    backend = get_backend(trw_dir)
    entry = backend.get(learning_id)
    return _memory_to_learning_dict(entry) if entry is not None else None


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
        _memory_to_learning_dict(entry)
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
        _memory_to_learning_dict(entry)
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


def update_access_tracking(trw_dir: Path, learning_ids: list[str]) -> None:
    """Increment access_count and last_accessed_at for recalled entries."""
    backend = get_backend(trw_dir)
    unique_ids = list(dict.fromkeys(lid for lid in learning_ids if lid))
    if not unique_ids:
        return
    now = datetime.now(timezone.utc)

    increment_access_counts = getattr(backend, "increment_access_counts", None)
    if callable(increment_access_counts):
        try:
            increment_access_counts(unique_ids, accessed_at=now)
            return
        except (StorageError, OSError, RuntimeError, sqlite3.Error, ValueError, TypeError):
            _warn("access_tracking_batch_update_failed", exc_info=True, entry_ids=unique_ids)

    for lid in unique_ids:
        try:
            entry = backend.get(lid)
            if entry is not None:
                backend.update(lid, access_count=entry.access_count + 1, last_accessed_at=now)
        except Exception:  # per-item: access tracking is best-effort, one failure must not break recall
            _warn("access_tracking_update_failed", exc_info=True, entry_id=lid)
            continue


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
    process (e.g. the shared HTTP server) still might. PASSIVE never resets the
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
    """Checkpoint the SQLite WAL when it exceeds threshold; fail-open.

    PRD-QUAL-050-FR05 + PRD-FIX-081: prefer a resetting TRUNCATE (to reclaim
    WAL file space) on the backend's single owning connection, which internally
    falls back to PASSIVE when readers hold pages (``busy=1``) or when the
    engine lacks the WAL-reset fix. When no backend owns the db in this process
    a bare PASSIVE checkpoint runs instead — PASSIVE never resets the WAL, so it
    is safe even if another process is writing concurrently.

    Returns a :class:`WalCheckpointResultDict`: a skip outcome (``skipped``),
    a success outcome with FR03 telemetry (``checkpointed``/``mode``/sizes), or
    a fail-open error outcome (``error``).
    """
    try:
        config = get_config()
        threshold_bytes = config.wal_checkpoint_threshold_mb * 1024 * 1024
        db_path = trw_dir / "memory" / "memory.db"
        wal_path = db_path.with_suffix(".db-wal")
        if not wal_path.exists():
            return {"skipped": True, "reason": "no_wal_file"}
        wal_size = wal_path.stat().st_size
        if wal_size < threshold_bytes:
            return {"skipped": True, "reason": "under_threshold"}
        wal_size_mb = round(wal_size / (1024 * 1024), 1)
        logger.info(
            "wal_checkpoint_starting",
            wal_size_mb=wal_size_mb,
            threshold_mb=config.wal_checkpoint_threshold_mb,
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
        if backend is not None and _same_db_path(backend.db_path, db_path):
            requested_truncate = True
            result: CheckpointResult = backend.checkpoint_wal("TRUNCATE")
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
        logger.info(
            "wal_checkpoint_complete",
            mode=mode,
            wal_size_before_mb=wal_size_mb,
            wal_size_after_mb=wal_size_after_mb,
            pages_checkpointed=checkpointed,
            busy=busy,
            truncate_busy=truncate_busy,
        )
        return {
            "checkpointed": True,
            "mode": mode,
            "wal_size_before_mb": wal_size_mb,
            "wal_size_after_mb": wal_size_after_mb,
            "pages_checkpointed": checkpointed,
            "busy": busy,
            "truncate_busy": truncate_busy,
        }
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        _warn("wal_checkpoint_failed", exc_info=True)
        return {"error": True, "reason": "checkpoint_failed"}
