"""Deferred delivery infrastructure for trw_deliver.

Orchestrator module — manages file-lock acquisition, audit logging,
the ``_run_deferred_steps`` orchestrator, and ``_launch_deferred``
thread launcher.

Step implementations live in domain-specific sub-modules:
- ``_deferred_steps_memory``: auto-prune, consolidation, tier sweep
- ``_deferred_steps_telemetry``: telemetry, batch send, ceremony feedback, checkpoint
- ``_deferred_steps_learning``: publish, outcome correlation, recall, trust, index sync

All step functions are re-exported here via ``X as X`` so that existing
test patches targeting ``trw_mcp.tools._deferred_delivery._step_foo``
continue to work without modification.

This module is internal (``_``-prefixed) — external code should import
from ``trw_mcp.tools.ceremony`` which re-exports the public surface.

Note: ``_deferred_thread`` and ``_deferred_lock`` live in ``ceremony.py``
(the canonical location) so that existing test patches via
``monkeypatch.setattr(cer, "_deferred_thread", ...)`` continue to work.
``_launch_deferred`` accesses them via a late import of the ceremony
module to avoid circular import issues at module-load time.
"""

from __future__ import annotations

import io
import json
import threading
import time

# Portability shim: fcntl is Unix-only. On Windows, advisory locking
# is a no-op (see persistence.py for rationale).
try:
    import fcntl as _fcntl

    def _lock_ex_nb(fd: int) -> None:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)

    def _lock_ex(fd: int) -> None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)

    def _lock_un(fd: int) -> None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
except ImportError:

    def _lock_ex_nb(fd: int) -> None:
        pass

    def _lock_ex(fd: int) -> None:
        pass

    def _lock_un(fd: int) -> None:
        pass
from datetime import datetime, timezone
from pathlib import Path

import structlog

# Re-export step functions from sub-modules so test patches on
# "trw_mcp.tools._deferred_delivery._step_foo" continue to work.
from trw_mcp.tools._deferred_steps_learning import _do_auto_progress as _do_auto_progress
from trw_mcp.tools._deferred_steps_learning import _do_index_sync as _do_index_sync
from trw_mcp.tools._deferred_steps_learning import _merge_session_events as _merge_session_events
from trw_mcp.tools._deferred_steps_learning import _step_auto_progress as _step_auto_progress
from trw_mcp.tools._deferred_steps_learning import _step_outcome_correlation as _step_outcome_correlation
from trw_mcp.tools._deferred_steps_learning import _step_publish_learnings as _step_publish_learnings
from trw_mcp.tools._deferred_steps_learning import _step_recall_outcome as _step_recall_outcome
from trw_mcp.tools._deferred_steps_learning import _step_trust_increment as _step_trust_increment
from trw_mcp.tools._deferred_steps_memory import _step_auto_prune as _step_auto_prune
from trw_mcp.tools._deferred_steps_memory import _step_consolidation as _step_consolidation
from trw_mcp.tools._deferred_steps_memory import _step_tier_sweep as _step_tier_sweep
from trw_mcp.tools._deferred_steps_telemetry import _step_batch_send as _step_batch_send
from trw_mcp.tools._deferred_steps_telemetry import _step_ceremony_feedback as _step_ceremony_feedback
from trw_mcp.tools._deferred_steps_telemetry import _step_checkpoint as _step_checkpoint
from trw_mcp.tools._deferred_steps_telemetry import _step_telemetry as _step_telemetry
from trw_mcp.tools._helpers import _run_step

logger = structlog.get_logger(__name__)


def _try_acquire_deferred_lock(trw_dir: Path) -> io.TextIOWrapper | None:
    """Try to acquire the deferred-deliver file lock (non-blocking).

    Returns the lock file handle on success, or None if another
    deferred batch is already running.  Caller MUST call
    ``_release_deferred_lock(fd)`` when done.
    """
    lock_path = trw_dir / "deliver-deferred.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("w", encoding="utf-8")
    try:
        _lock_ex_nb(fd.fileno())
        # Write PID + timestamp as valid JSON so operators can inspect
        import os as _os

        fd.write(json.dumps({"pid": _os.getpid(), "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
        fd.flush()
        return fd
    except Exception:  # justified: cleanup, lock acquisition failure releases fd and returns None
        fd.close()
        return None


def _release_deferred_lock(fd: object) -> None:
    """Release the deferred-deliver file lock."""
    try:
        import io as _io

        if isinstance(fd, _io.TextIOWrapper):
            _lock_un(fd.fileno())
            fd.close()
    except Exception:  # justified: fail-open, lock release cleanup
        # justified: lock release is best-effort cleanup -- failing here
        # only means the lock file persists until process exit.
        logger.debug("lock_release_failed", exc_info=True)


def _log_deferred_result(
    trw_dir: Path,
    results: dict[str, object],
    errors: list[str],
) -> None:
    """Append deferred step results to an audit log."""
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": {k: v for k, v in results.items() if k != "timestamp"},
        "errors": errors,
        "success": len(errors) == 0,
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            _lock_ex(f.fileno())
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            _lock_un(f.fileno())
    except Exception:  # justified: fail-open, deferred log is diagnostic only
        logger.debug("deferred_log_write_failed", exc_info=True)


def _run_deferred_steps(
    trw_dir: Path,
    resolved_run: Path | None,
    critical_results: dict[str, object],
    *,
    skip_index_sync: bool = False,
) -> None:
    """Execute deferred delivery steps in the background.

    Acquires a non-blocking file lock to prevent concurrent deferred batches.
    Each step is fail-open -- failures are logged but don't block other steps.

    Test patches should target this module directly:
    ``patch("trw_mcp.tools._deferred_delivery._step_foo")``.
    """
    lock_fd = _try_acquire_deferred_lock(trw_dir)
    if lock_fd is None:
        logger.info("deferred_deliver_skipped", reason="another_batch_running")
        return

    results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    t0 = time.monotonic()

    def _timed_step(name: str, fn: object) -> None:
        """Run a deferred step with per-step timing and structured logging."""
        _t = time.monotonic()
        _pre_errors = len(errors)
        _run_step(name, fn, results, errors)  # type: ignore[arg-type]
        _duration_ms = round((time.monotonic() - _t) * 1000, 1)
        _step_result = results.get(name)
        if len(errors) > _pre_errors:
            _last_err = errors[-1]
            logger.error("deferred_step_failed", step=name, error=_last_err)
        elif _step_result is None or (isinstance(_step_result, dict) and _step_result.get("status") == "skipped"):
            logger.warning("deferred_step_skip", step=name, reason=str(_step_result))
        else:
            logger.info("deferred_step_ok", step=name, duration_ms=_duration_ms)

    try:
        _timed_step("auto_prune", lambda: _step_auto_prune(trw_dir))
        _timed_step("consolidation", lambda: _step_consolidation(trw_dir))
        _timed_step("tier_sweep", lambda: _step_tier_sweep(trw_dir))

        if not skip_index_sync:
            _timed_step("index_sync", lambda: _do_index_sync())
        else:
            results["index_sync"] = {"status": "skipped"}
            logger.warning("deferred_step_skip", step="index_sync", reason="skip_index_sync=True")

        _timed_step("auto_progress", lambda: _step_auto_progress(resolved_run))
        _timed_step("publish_learnings", lambda: _step_publish_learnings())
        _timed_step("outcome_correlation", lambda: _step_outcome_correlation())
        _timed_step("recall_outcome", lambda: _step_recall_outcome(resolved_run))
        _timed_step("telemetry", lambda: _step_telemetry(resolved_run))
        _timed_step("batch_send", lambda: _step_batch_send())
        _timed_step("trust_increment", lambda: _step_trust_increment(resolved_run))
        _timed_step("ceremony_feedback", lambda: _step_ceremony_feedback(resolved_run, critical_results))

        steps_ok = sum(
            1 for k, v in results.items()
            if k not in ("timestamp", "elapsed_seconds")
            and isinstance(v, dict)
            and v.get("status") not in ("skipped", "error", None)
        )
        steps_failed = len(errors)
        elapsed = time.monotonic() - t0
        results["elapsed_seconds"] = round(elapsed, 2)
        logger.info(
            "deferred_delivery_complete",
            steps_ok=steps_ok,
            steps_failed=steps_failed,
        )
        logger.info(
            "deferred_deliver_complete",
            steps=len(results) - 2,  # minus timestamp and elapsed
            errors=len(errors),
            elapsed=round(elapsed, 2),
        )
    except Exception as exc:  # justified: fail-open, deferred delivery must never crash the background thread
        errors.append(f"deferred_fatal: {exc}")
        logger.warning("deferred_deliver_fatal", error=str(exc), exc_info=True)
    finally:
        _log_deferred_result(trw_dir, results, errors)
        _release_deferred_lock(lock_fd)


def _launch_deferred(
    trw_dir: Path,
    resolved_run: Path | None,
    critical_results: dict[str, object],
    *,
    skip_index_sync: bool = False,
) -> str:
    """Launch deferred steps on a daemon thread.

    Returns a status string indicating what happened:
    - "launched": new background thread started
    - "skipped_already_running": a previous deferred batch is still active

    Note: ``_deferred_thread`` and ``_deferred_lock`` are read from/written
    to the ``ceremony`` module (late import) so that test patches via
    ``monkeypatch.setattr(cer, "_deferred_thread", ...)`` work correctly.
    """
    import trw_mcp.tools.ceremony as _cer  # late import to avoid circular

    with _cer._deferred_lock:
        if _cer._deferred_thread is not None and _cer._deferred_thread.is_alive():
            logger.info("deferred_launch_skipped", reason="thread_still_alive")
            return "skipped_already_running"

        _cer._deferred_thread = threading.Thread(
            target=_run_deferred_steps,
            args=(trw_dir, resolved_run, critical_results),
            kwargs={"skip_index_sync": skip_index_sync},
            name="trw-deliver-deferred",
            daemon=True,
        )
        _cer._deferred_thread.start()
        return "launched"
