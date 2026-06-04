"""Memory adapter — embedding status + corruption recovery helpers.

Belongs to the ``memory_adapter.py`` facade. Re-exported there for back-compat.

Two micro-clusters:
1. **Embedding status** (3 helpers) — read/reset/inject the embed-failure
   counter. ``check_embeddings_status`` honors the
   ``memory_adapter._embed_failures`` test-injection override at call time.
2. **Corruption recovery** (3 helpers) — detect SQLite corruption,
   force-recover, and reset the singleton. Triggered from CRUD code paths
   when ``_is_corruption_error`` matches.

Extracted as DIST-243 batch 44 to bring the parent ``memory_adapter.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import threading
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog
from trw_memory.exceptions import CorruptDatabaseUnsalvageableError

logger = structlog.get_logger(__name__)


_MALFORMED_MARKERS = ("malformed", "database disk image", "not a database", "file is not a database")
_RECOVERY_LOCK = threading.Lock()
_RECOVERY_THREAD: threading.Thread | None = None


def check_embeddings_status(
    *,
    allow_initialize: bool = True,
    coverage_probe: bool = False,
) -> dict[str, object]:
    """Check embedding readiness; honors memory_adapter._embed_failures override.

    Args:
        allow_initialize: Whether to initialize embedder if not yet checked.
        coverage_probe: When True, probe vector coverage ratio (PRD-FIX-COMPOUNDING-3-FR02).
    """
    from trw_mcp.state import memory_adapter
    from trw_mcp.state._memory_connection import check_embeddings_status as _impl

    result = _impl(allow_initialize=allow_initialize, coverage_probe=coverage_probe)
    if memory_adapter._embed_failures is not None:
        result["recent_failures"] = memory_adapter._embed_failures
    return result


def reset_embed_failure_count() -> None:
    """Reset the embed failure counter and clear the facade-level override."""
    from trw_mcp.state import memory_adapter
    from trw_mcp.state._memory_connection import reset_embed_failure_count as _impl

    _impl()
    memory_adapter._embed_failures = None


def set_embed_failure_count_for_testing(n: int) -> None:
    """Set the facade-level embed failure override (for tests only)."""
    from trw_mcp.state import memory_adapter

    memory_adapter._embed_failures = n


def _is_corruption_error(exc: BaseException) -> bool:
    """Return True if *exc* indicates SQLite database corruption."""
    if isinstance(exc, CorruptDatabaseUnsalvageableError):
        return False
    msg = str(exc).lower()
    return any(m in msg for m in _MALFORMED_MARKERS)


def _log_terminal_recovery(db_path: Path, exc: CorruptDatabaseUnsalvageableError) -> None:
    """Log strict recovery refusal before surfacing it to the caller."""
    _logger().error(
        "memory_recovery_terminal",
        db=str(db_path),
        backup_path=exc.backup_path,
        action="raise",
    )


def _memory_recovery_in_progress() -> bool:
    """Return True when this process already has a recovery worker running."""
    with _RECOVERY_LOCK:
        return _RECOVERY_THREAD is not None and _RECOVERY_THREAD.is_alive()


def _schedule_deferred_recovery(
    trw_dir: Path,
    *,
    reason: str,
    context: dict[str, object] | None = None,
) -> bool:
    """Start one background memory recovery worker, if one is not active."""
    global _RECOVERY_THREAD
    payload = dict(context or {})
    with _RECOVERY_LOCK:
        if _RECOVERY_THREAD is not None and _RECOVERY_THREAD.is_alive():
            _logger().warning(
                "memory_recovery_deferred_duplicate",
                trw_dir=str(trw_dir),
                reason=reason,
                **payload,
            )
            return False
        thread = threading.Thread(
            target=_run_deferred_recovery,
            args=(trw_dir, reason, payload),
            name="trw-memory-recovery",
            daemon=True,
        )
        _RECOVERY_THREAD = thread
        thread.start()

    _logger().warning(
        "memory_recovery_deferred_scheduled",
        trw_dir=str(trw_dir),
        reason=reason,
        **payload,
    )
    return True


def _run_deferred_recovery(trw_dir: Path, reason: str, context: dict[str, object]) -> None:
    """Run recovery outside the foreground MCP request path."""
    global _RECOVERY_THREAD
    started = perf_counter()
    logger = _logger()
    logger.warning(
        "memory_recovery_deferred_start",
        trw_dir=str(trw_dir),
        reason=reason,
        **context,
    )
    try:
        _recover_and_reset_backend(trw_dir)
    except CorruptDatabaseUnsalvageableError as exc:
        logger.exception(
            "memory_recovery_deferred_terminal",
            trw_dir=str(trw_dir),
            reason=reason,
            backup_path=exc.backup_path,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            **context,
        )
    except Exception:  # justified: fail-open background recovery must never crash the MCP server
        logger.exception(
            "memory_recovery_deferred_failed",
            trw_dir=str(trw_dir),
            reason=reason,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            **context,
        )
    else:
        logger.warning(
            "memory_recovery_deferred_complete",
            trw_dir=str(trw_dir),
            reason=reason,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            **context,
        )
    finally:
        current = threading.current_thread()
        with _RECOVERY_LOCK:
            if _RECOVERY_THREAD is current:
                _RECOVERY_THREAD = None


def _recover_and_reset_backend(trw_dir: Path) -> None:
    """Force-recover the database, backfill from YAML, and reset the singleton."""
    from trw_mcp.state._memory_connection import get_backend as _get_backend
    from trw_mcp.state._memory_connection import reset_backend as _reset

    db_path = trw_dir / "memory" / "memory.db"
    _logger().error("runtime_corruption_detected", db=str(db_path), action="recover_and_reset")
    _reset()
    if db_path.exists():
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        try:
            conn = SQLiteBackend.recover_db(db_path)
        except CorruptDatabaseUnsalvageableError as exc:
            _log_terminal_recovery(db_path, exc)
            raise
        conn.close()
    sentinel = trw_dir / "memory" / ".migrated"
    if sentinel.exists():
        sentinel.unlink()
    try:
        backend = _get_backend(trw_dir)
    except CorruptDatabaseUnsalvageableError as exc:
        _log_terminal_recovery(db_path, exc)
        raise
    # PRD-FIX-COMPOUNDING-3-FR03: Schedule vector backfill after deferred recovery.
    # get_backend() already fires _schedule_post_recovery_backfill when
    # backend.recovered==True. Call it again here explicitly to handle the case
    # where the newly created backend does NOT have recovered==True (because
    # recovery_db was called manually above), ensuring the deferred-background
    # recovery path always triggers backfill.
    if not getattr(backend, "recovered", False):
        from trw_mcp.state._memory_connection import _schedule_post_recovery_backfill

        _schedule_post_recovery_backfill(trw_dir)


def _logger() -> Any:
    """Lookup memory_adapter.logger at call time so test patches stick."""
    from trw_mcp.state import memory_adapter

    return memory_adapter.logger
