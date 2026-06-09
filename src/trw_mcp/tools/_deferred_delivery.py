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

import atexit
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

import trw_mcp.tools._deferred_state as _ds
from trw_mcp._locking import _lock_ex, _lock_ex_nb, _lock_un
from trw_mcp.models.config import get_config
from trw_mcp.tools import _deferred_locking as _dl
from trw_mcp.tools._deferred_locking import (
    _is_lock_record_stale as _is_lock_record_stale,
    _peek_deferred_lock_holder as _peek_deferred_lock_holder,
    _release_deferred_lock as _release_deferred_lock,
)

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


def _try_acquire_deferred_lock(
    trw_dir: Path,
    *,
    stale_threshold_seconds: float = 600.0,
) -> object:
    """Compatibility wrapper preserving the legacy ``_lock_ex_nb`` patch seam."""
    helper_vars = vars(_dl)
    original_lock = helper_vars["_lock_ex_nb"]
    helper_vars["_lock_ex_nb"] = _lock_ex_nb
    try:
        return _dl._try_acquire_deferred_lock(
            trw_dir,
            stale_threshold_seconds=stale_threshold_seconds,
        )
    finally:
        helper_vars["_lock_ex_nb"] = original_lock


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

        # F19 (2026-06-04): the audit-pattern "promotion candidates" used to be
        # mirrored into dedicated ``audit_pattern_promotions`` /
        # ``promotion_candidates`` run.yaml keys here, tagged
        # ``promotion_path="metadata_only"`` / ``meta_tune_integration="tool_unavailable"``.
        # That was a self-documented no-op: nothing in the codebase ever read
        # those keys back (CORE-093 removed automatic CLAUDE.md learning
        # promotion, and no trw_meta_tune() tool ships), so the signal was
        # computed, written, and silently dropped. The arrays also ballooned
        # legacy run.yaml files to multiple MB and dominated boot-time YAML
        # parsing (see state/_run_gc.py). Wiring them into trw_instructions_sync
        # would re-introduce exactly the CLAUDE.md promotion CORE-093 deleted, so
        # the honest fix is to stop persisting the dead signal. The consolidation
        # step's status still flows through ``deferred_results`` above for audit.

        writer.write_yaml(run_yaml_path, run_data)
        logger.info("deferred_results_persisted", path=str(run_yaml_path))
    except Exception:  # justified: fail-open, deferred state persistence is best-effort
        logger.warning("deferred_results_persist_failed", exc_info=True)


def _log_deferred_result(
    trw_dir: Path,
    results: dict[str, object],
    errors: list[str],
) -> None:
    """Append deferred step results to an audit log."""
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # PRD-FIX-085 FR04: rotate at 10 MB to match surface_tracking parity.
    # Pre-fix this file grew unbounded -- observed 25 MB on the dev repo.
    from trw_mcp.state._helpers import rotate_jsonl

    rotate_jsonl(log_path, max_bytes=10 * 1024 * 1024)

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


def _resolve_step_budgets() -> tuple[float, float]:
    """Read per-step and per-batch budgets from config, with safe defaults.

    Reading happens inside ``_run_deferred_steps`` so the config can be
    monkeypatched between tests. Defaults match the values in
    ``_fields_build.py``.
    """
    try:
        from trw_mcp.models.config import get_config

        cfg = get_config()
        step_s = float(getattr(cfg, "deferred_step_max_seconds", 60))
        batch_s = float(getattr(cfg, "deferred_batch_max_seconds", 300))
    except Exception:  # justified: fail-open, config load failure must not block the deferred batch
        step_s, batch_s = 60.0, 300.0
    # A non-positive budget disables the watchdog (escape hatch for ops
    # who need an unbounded batch). Mirror Python's ``threading.Timer``
    # behavior on 0.0 to mean "fire immediately"; we treat 0 as disabled.
    return max(step_s, 0.0), max(batch_s, 0.0)


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

    Watchdog: per-step and per-batch wall-clock budgets are enforced by a
    ``threading.Timer`` that flips ``_ds._cancel_event`` on overrun.
    Cooperative steps (``auto_prune`` polls it between dedup batches) stop
    voluntarily and return partial results. Non-cooperative steps still
    log an ``overrun`` warning so operators can see which step ran long.
    Once the cancel event is set, the orchestrator skips remaining steps
    with ``status=cancelled_batch_budget`` so the batch can finalise the
    lock release in bounded time.

    Test patches should target this module directly:
    ``patch("trw_mcp.tools._deferred_delivery._step_foo")``.
    """
    lock_fd = _try_acquire_deferred_lock(
        trw_dir,
        stale_threshold_seconds=float(get_config().deferred_lock_stale_seconds),
    )
    if lock_fd is None:
        logger.info("deferred_deliver_skipped", reason="another_batch_running")
        return {"status": "skipped", "reason": "another_batch_running"}

    # Reset cancellation state for this batch. Previous batches may have
    # set the event when they hit a budget; new batches start clean.
    _ds._cancel_event.clear()

    results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    t0 = time.monotonic()
    step_budget_s, batch_budget_s = _resolve_step_budgets()
    batch_deadline_at = (t0 + batch_budget_s) if batch_budget_s > 0 else None

    # Per-batch watchdog: flips the cancel event when the whole batch
    # exceeds its budget. Daemon timer so process exit doesn't wait for it.
    batch_watchdog: threading.Timer | None = None

    def _batch_overrun() -> None:
        elapsed = time.monotonic() - t0
        logger.warning(
            "deferred_batch_budget_exceeded",
            budget_seconds=batch_budget_s,
            elapsed_seconds=round(elapsed, 2),
        )
        _ds._cancel_event.set()

    if batch_budget_s > 0:
        batch_watchdog = threading.Timer(batch_budget_s, _batch_overrun)
        batch_watchdog.daemon = True
        batch_watchdog.start()

    def _timed_step(name: str, fn: object) -> None:
        """Run a deferred step with per-step timing and per-step watchdog.

        Skips the step early when the cancel event is already set (because
        an earlier step blew the batch budget). Spawns a per-step
        ``threading.Timer`` that logs an overrun and flips the cancel
        event if the step exceeds ``step_budget_s``.
        """
        if _ds._cancel_event.is_set():
            results[name] = {"status": "cancelled_batch_budget"}
            logger.warning("deferred_step_cancelled", step=name, reason="batch_budget_exceeded")
            return

        step_t0 = time.monotonic()

        def _step_overrun() -> None:
            elapsed = time.monotonic() - step_t0
            logger.warning(
                "deferred_step_budget_exceeded",
                step=name,
                budget_seconds=step_budget_s,
                elapsed_seconds=round(elapsed, 2),
            )
            _ds._cancel_event.set()

        step_watchdog: threading.Timer | None = None
        if step_budget_s > 0:
            step_watchdog = threading.Timer(step_budget_s, _step_overrun)
            step_watchdog.daemon = True
            step_watchdog.start()

        _pre_errors = len(errors)
        try:
            _run_step(name, fn, results, errors)  # type: ignore[arg-type]  # justified: fn is Callable at runtime; object annotation avoids Callable import in closure scope
        finally:
            if step_watchdog is not None:
                step_watchdog.cancel()
        _duration_ms = round((time.monotonic() - step_t0) * 1000, 1)
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
        # FIX-052: feed the LIVE deferred ``results`` (which already contains the
        # ``telemetry`` step's computed ceremony_score, build_passed, and
        # coverage_delta) rather than the PRE-deferred ``critical_results``
        # snapshot. The snapshot never carried a ``telemetry`` key, so the
        # feedback step recorded a constant ceremony_score=0.0 and the adaptive
        # feedback loop had no gradient. The ``telemetry`` step runs above, so its
        # output is present in ``results`` by the time this step executes.
        _merge_results: dict[str, object] = dict(critical_results)
        _merge_results.update(results)
        _timed_step("ceremony_feedback", lambda: _step_ceremony_feedback(resolved_run, _merge_results))

        # Sprint 84: Delivery metrics (PRD-CORE-104)
        _timed_step("delivery_metrics", lambda: _step_delivery_metrics(trw_dir, resolved_run))

        metrics_result = results.get("delivery_metrics")
        if isinstance(metrics_result, dict):
            from trw_mcp.state.persistence import FileStateReader

            rework_metrics = _step_collect_rework_metrics(resolved_run, FileStateReader())
            metrics_result.update(rework_metrics)
            _persist_session_metrics(metrics_result, resolved_run)
        # Surface the watchdog outcome alongside the step results so the
        # audit log records WHY a batch returned early.
        if _ds._cancel_event.is_set():
            results["watchdog"] = {
                "status": "cancelled",
                "batch_budget_seconds": batch_budget_s,
                "step_budget_seconds": step_budget_s,
                "elapsed_seconds": round(time.monotonic() - t0, 2),
                "batch_deadline_at_monotonic": batch_deadline_at,
            }

        steps_ok = sum(
            1
            for k, v in results.items()
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
        # Cancel the per-batch watchdog before we drop the file lock so it
        # can't fire after the batch already exited and spuriously flip
        # the cancel event for the next batch's first step.
        if batch_watchdog is not None:
            batch_watchdog.cancel()
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

    PRD-FIX-088 FR01 "Shutdown + recovery contract": before launching the
    deferred-delivery thread, join any running Q-learning bg worker so
    the last correlation pass is durable on disk before the deliver
    batch starts. Daemon threads on process exit aren't guaranteed to
    finish their current SQLite write, and ``trw_deliver`` is the
    last-pass contract.
    """
    # Lazy import: avoid pulling the build-tools package into the
    # _deferred_delivery import graph at module-load time.
    from trw_mcp.tools._q_learning_state import join_q_learning_worker

    join_q_learning_worker(timeout=30.0)

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
        # PRD-FIX-088: the deferred thread is a daemon so a stuck step can never
        # hang process shutdown — but daemon threads are killed mid-write on
        # interpreter exit, silently losing pending learning/delivery work
        # (publish, outcome correlation, index sync). Register a bounded atexit
        # join so the in-flight batch flushes durably in the normal-exit case.
        _register_deferred_atexit_join()
        return "launched"


# atexit registration is idempotent-by-flag: register the join hook at most once
# per process so repeated trw_deliver calls do not stack handlers.
_atexit_join_registered = False

# Bound the shutdown join so a genuinely stuck deferred step (held by the
# cooperative cancel watchdog otherwise) cannot wedge interpreter exit forever.
_DEFERRED_ATEXIT_JOIN_TIMEOUT_S = 30.0


def _register_deferred_atexit_join() -> None:
    """Register a one-shot atexit hook that flushes the deferred thread.

    Must be called while holding ``_ds._deferred_lock`` (it is, from
    ``_launch_deferred``) so the registration flag is mutated race-free.
    """
    global _atexit_join_registered
    if _atexit_join_registered:
        return
    atexit.register(_join_deferred_thread_at_exit)
    _atexit_join_registered = True


def _join_deferred_thread_at_exit() -> None:
    """Flush any in-flight deferred-delivery batch on process exit.

    Signals cooperative cancellation first so long-running steps return at
    their next poll, then joins with a bounded timeout. A daemon thread that
    is killed mid-write loses pending learnings; this join makes the
    normal-exit path durable while the timeout preserves the "shutdown can
    never wedge" guarantee.
    """
    thread = _ds._deferred_thread
    if thread is None or not thread.is_alive():
        return
    logger.info("deferred_atexit_join_start", thread=thread.name)
    thread.join(timeout=_DEFERRED_ATEXIT_JOIN_TIMEOUT_S)
    if thread.is_alive():
        logger.warning(
            "deferred_atexit_join_timeout",
            thread=thread.name,
            timeout_s=_DEFERRED_ATEXIT_JOIN_TIMEOUT_S,
        )
    else:
        logger.info("deferred_atexit_join_complete", thread=thread.name)
