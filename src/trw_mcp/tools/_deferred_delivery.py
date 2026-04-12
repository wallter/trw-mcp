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

``_deferred_thread`` and ``_deferred_lock`` live in ``_deferred_state.py``
(extracted to break the ceremony <-> _deferred_delivery circular import).
"""

# ruff: noqa: I001

from __future__ import annotations

import io
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

import trw_mcp.tools._deferred_state as _ds
from trw_mcp._locking import _lock_ex, _lock_ex_nb, _lock_un

# Re-export step functions from sub-modules so test patches on
# "trw_mcp.tools._deferred_delivery._step_foo" continue to work.
from trw_mcp.tools._deferred_steps_learning import (
    _do_auto_progress as _do_auto_progress,
    _do_index_sync as _do_index_sync,
    _merge_session_events as _merge_session_events,
    _step_auto_progress as _step_auto_progress,
    _step_collect_rework_metrics as _step_collect_rework_metrics,
    _step_delivery_metrics as _step_delivery_metrics,
    _step_outcome_correlation as _step_outcome_correlation,
    _step_publish_learnings as _step_publish_learnings,
    _step_recall_outcome as _step_recall_outcome,
    _step_trust_increment as _step_trust_increment,
)
from trw_mcp.tools._deferred_steps_memory import (
    _step_auto_prune as _step_auto_prune,
    _step_consolidation as _step_consolidation,
    _step_tier_sweep as _step_tier_sweep,
)
from trw_mcp.tools._deferred_steps_telemetry import (
    _step_batch_send as _step_batch_send,
    _step_ceremony_feedback as _step_ceremony_feedback,
    _step_checkpoint as _step_checkpoint,
    _step_telemetry as _step_telemetry,
)
from trw_mcp.tools._helpers import _run_step

logger = structlog.get_logger(__name__)


def _persist_session_metrics(
    metrics_result: dict[str, object],
    resolved_run: Path | None,
) -> None:
    """Persist session_metrics to run.yaml after delivery metrics step.

    PRD-CORE-104: Writes the delivery metrics result dict into
    run.yaml under the ``session_metrics`` key so that downstream
    consumers (meta-tune, dashboards) can access session-level
    reward signals without re-computing them.

    Fail-open: errors are logged but never raised.
    """
    if resolved_run is None:
        return
    if not isinstance(metrics_result, dict) or metrics_result.get("status") != "success":
        return
    try:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        writer = FileStateWriter()
        run_yaml_path = resolved_run / "meta" / "run.yaml"
        if run_yaml_path.exists():
            run_data = reader.read_yaml(run_yaml_path)
            run_data["session_metrics"] = metrics_result
            writer.write_yaml(run_yaml_path, run_data)
            logger.info("session_metrics_persisted", path=str(run_yaml_path))
    except Exception:  # justified: fail-open, session metrics persistence is best-effort
        logger.warning("session_metrics_persist_failed", exc_info=True)


def _persist_deferred_results(
    results: dict[str, object],
    resolved_run: Path | None,
) -> None:
    """Persist deferred delivery results to run.yaml for downstream consumers."""
    if resolved_run is None:
        return
    try:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        writer = FileStateWriter()
        run_yaml_path = resolved_run / "meta" / "run.yaml"
        if not run_yaml_path.exists():
            return

        run_data = reader.read_yaml(run_yaml_path)
        run_data["deferred_results"] = dict(results)

        consolidation = results.get("consolidation")
        if isinstance(consolidation, dict):
            promotions = consolidation.get("audit_pattern_promotions")
            if isinstance(promotions, list):
                run_data["audit_pattern_promotions"] = promotions
                run_data["promotion_candidates"] = {
                    "source": "consolidation",
                    "audit_pattern_promotions": promotions,
                    "audit_pattern_promotion_threshold": consolidation.get("audit_pattern_promotion_threshold"),
                    # PRD-QUAL-056-FR10: make the current production wiring
                    # explicit. CORE-093 removed automatic CLAUDE.md learning
                    # promotion, and there is no shipped trw_meta_tune() tool.
                    # These candidates are currently persisted as delivery
                    # metadata for later reconciliation/follow-up, not auto-
                    # promoted into another surface.
                    "promotion_path": "metadata_only",
                    "delivery_surface": "run.yaml",
                    "claude_md_sync_integration": "not_applicable_prd_core_093",
                    "meta_tune_integration": "tool_unavailable",
                }
                logger.info(
                    "audit_pattern_promotions_persisted",
                    count=len(promotions),
                    promotion_path="metadata_only",
                    delivery_surface="run.yaml",
                    claude_md_sync_integration="not_applicable_prd_core_093",
                    meta_tune_integration="tool_unavailable",
                )

        writer.write_yaml(run_yaml_path, run_data)
        logger.info("deferred_results_persisted", path=str(run_yaml_path))
    except Exception:  # justified: fail-open, deferred state persistence is best-effort
        logger.warning("deferred_results_persist_failed", exc_info=True)


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
) -> dict[str, object]:
    """Execute deferred delivery steps in the background.

    Acquires a non-blocking file lock to prevent concurrent deferred batches.
    Each step is fail-open -- failures are logged but don't block other steps.

    Test patches should target this module directly:
    ``patch("trw_mcp.tools._deferred_delivery._step_foo")``.
    """
    lock_fd = _try_acquire_deferred_lock(trw_dir)
    if lock_fd is None:
        logger.info("deferred_deliver_skipped", reason="another_batch_running")
        return {"status": "skipped", "reason": "another_batch_running"}

    results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    t0 = time.monotonic()

    def _timed_step(name: str, fn: object) -> None:
        """Run a deferred step with per-step timing and structured logging."""
        _t = time.monotonic()
        _pre_errors = len(errors)
        _run_step(name, fn, results, errors)  # type: ignore[arg-type]  # justified: fn is Callable at runtime; object annotation avoids Callable import in closure scope
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

        # Sprint 84: Delivery metrics (PRD-CORE-104)
        _timed_step("delivery_metrics", lambda: _step_delivery_metrics(trw_dir, resolved_run))

        metrics_result = results.get("delivery_metrics")
        if isinstance(metrics_result, dict):
            from trw_mcp.state.persistence import FileStateReader

            rework_metrics = _step_collect_rework_metrics(resolved_run, FileStateReader())
            metrics_result.update(rework_metrics)
            _persist_session_metrics(metrics_result, resolved_run)

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
        _persist_deferred_results(results, resolved_run)
        _log_deferred_result(trw_dir, results, errors)
        _release_deferred_lock(lock_fd)
    return results


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

    Thread handle and lock live in ``_deferred_state`` (extracted to
    break the ceremony <-> _deferred_delivery circular import).
    Test patches should target ``trw_mcp.tools._deferred_state``.
    """
    with _ds._deferred_lock:
        if _ds._deferred_thread is not None and _ds._deferred_thread.is_alive():
            logger.info("deferred_launch_skipped", reason="thread_still_alive")
            return "skipped_already_running"

        _ds._deferred_thread = threading.Thread(
            target=_run_deferred_steps,
            args=(trw_dir, resolved_run, critical_results),
            kwargs={"skip_index_sync": skip_index_sync},
            name="trw-deliver-deferred",
            daemon=True,
        )
        _ds._deferred_thread.start()
        return "launched"
