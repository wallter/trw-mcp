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
from trw_mcp.tools._deferred_persistence import (
    _persist_deferred_results as _persist_deferred_results,
    _persist_session_metrics as _persist_session_metrics,
    _resolve_step_budgets as _resolve_step_budgets,
    log_deferred_result,
)


from trw_mcp.tools._helpers import _run_step

logger = structlog.get_logger(__name__)

# Single source of truth for the deferred-delivery step roster. The
# ``_run_deferred_steps`` loop is DRIVEN by this tuple, so the steps that
# actually run ARE exactly these names — and ``DEFERRED_STEP_COUNT`` (surfaced
# by ``trw_deliver`` as the ``deferred_steps`` field) is DERIVED from it and can
# never drift out of sync with the executed count again. Adding or removing a
# step means editing this tuple (and the matching ``step_map`` entry inside
# ``_run_deferred_steps``); the reported count follows automatically.
DEFERRED_STEPS: tuple[str, ...] = (
    "auto_prune",
    "consolidation",
    "tier_sweep",
    "index_sync",
    "auto_progress",
    "publish_learnings",
    "outcome_correlation",
    "recall_outcome",
    "telemetry",
    "batch_send",
    "trust_increment",
    "ceremony_feedback",
    "delivery_metrics",
)
DEFERRED_STEP_COUNT = len(DEFERRED_STEPS)


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


def _log_deferred_result(trw_dir: Path, results: dict[str, object], errors: list[str]) -> None:
    """Append deferred results while preserving the legacy lock patch seam."""
    log_deferred_result(trw_dir, results, errors, _lock_ex, _lock_un)


def _run_deferred_steps(
    trw_dir: Path,
    resolved_run: Path | None,
    critical_results: dict[str, object],
    *,
    skip_index_sync: bool = False,
    operation_id: str = "",
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

    # Map each roster name to its call-time thunk. Built here (not module-level)
    # because the thunks close over trw_dir / resolved_run / critical_results /
    # results. Each references the module-global ``_step_*`` names so test
    # monkeypatches on ``_deferred_delivery._step_foo`` still bind at call time.
    step_map: dict[str, object] = {
        "auto_prune": lambda: _step_auto_prune(trw_dir),
        "consolidation": lambda: _step_consolidation(trw_dir),
        "tier_sweep": lambda: _step_tier_sweep(trw_dir),
        "index_sync": lambda: _do_index_sync(),
        "auto_progress": lambda: _step_auto_progress(resolved_run),
        "publish_learnings": lambda: _step_publish_learnings(),
        "outcome_correlation": lambda: _step_outcome_correlation(),
        "recall_outcome": lambda: _step_recall_outcome(resolved_run),
        "telemetry": lambda: _step_telemetry(resolved_run),
        "batch_send": lambda: _step_batch_send(),
        "trust_increment": lambda: _step_trust_increment(resolved_run),
        # FIX-052: feed the LIVE deferred ``results`` (which by the time this
        # step runs already contains the ``telemetry`` step's computed
        # ceremony_score, build_passed, and coverage_delta) merged OVER the
        # PRE-deferred ``critical_results`` snapshot. The snapshot alone never
        # carried a ``telemetry`` key, so the feedback step recorded a constant
        # ceremony_score=0.0 and the adaptive feedback loop had no gradient. The
        # merge is evaluated at call time inside the thunk (telemetry runs
        # earlier in the roster), so ``results`` is fully populated when it runs.
        "ceremony_feedback": lambda: _step_ceremony_feedback(resolved_run, {**dict(critical_results), **results}),
        # Sprint 84: Delivery metrics (PRD-CORE-104)
        "delivery_metrics": lambda: _step_delivery_metrics(trw_dir, resolved_run),
    }

    # PRD-CORE-208 FR02/FR06: journal each roster step over the already-claimed
    # operation so a process death mid-batch (e.g. after the NON_REPLAYABLE trust
    # increment) leaves a durable ``started`` step for FR04 recovery. Fully
    # fail-open — a disabled journal makes every ``.step`` a no-op.
    from trw_mcp.tools._delivery_journal_wiring import open_deferred_journal
    from trw_mcp.tools._delivery_models import OperationState
    from trw_mcp.tools._delivery_tracer import DEFERRED_STEP_EFFECT_IDS

    deferred_journal = open_deferred_journal(trw_dir, operation_id)
    deferred_journal.mark_state(OperationState.DEFERRED_RUNNING)

    try:
        for _step_name in DEFERRED_STEPS:
            if _step_name == "index_sync" and skip_index_sync:
                results["index_sync"] = {"status": "skipped"}
                logger.warning("deferred_step_skip", step="index_sync", reason="skip_index_sync=True")
                continue
            with deferred_journal.step(DEFERRED_STEP_EFFECT_IDS.get(_step_name, "")):
                _timed_step(_step_name, step_map[_step_name])

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
        # Count of roster steps that actually executed (each ``_timed_step`` /
        # ``_run_step`` writes ``results[name]`` for its roster entry, incl.
        # skipped/cancelled/failed statuses). Deriving from DEFERRED_STEPS keeps
        # this immune to non-step bookkeeping keys (timestamp, elapsed_seconds,
        # watchdog): ``len(results) - 2`` drifted the moment such a key was added
        # (reported 12 while DEFERRED_STEP_COUNT is 13). In a full batch this
        # equals DEFERRED_STEP_COUNT.
        executed_steps = sum(1 for _name in DEFERRED_STEPS if _name in results)
        logger.info(
            "deferred_delivery_complete",
            steps_ok=steps_ok,
            steps_failed=steps_failed,
        )
        logger.info(
            "deferred_deliver_complete",
            steps=executed_steps,
            errors=len(errors),
            elapsed=round(elapsed, 2),
        )
    except Exception as exc:  # justified: fail-open, deferred delivery must never crash the background thread
        errors.append(f"deferred_fatal: {exc}")
        logger.warning("deferred_deliver_fatal", error=str(exc), exc_info=True)
    finally:
        # PRD-CORE-208 FR02: the operation is not complete when only the
        # synchronous effects have finished.  Commit the aggregate terminal
        # state after the deferred roster has actually stopped so status,
        # idempotent retries, and retention all observe the same truth.
        if not deferred_journal.wait_for_step_terminal("S20"):
            errors.append("delivery_journal_terminal_failed: timed out waiting for synchronous S20")
        terminal_state = (
            OperationState.CANCELLED
            if _ds._cancel_event.is_set()
            else OperationState.FAILED
            if errors
            else OperationState.SUCCEEDED
        )
        try:
            deferred_journal.mark_state(terminal_state)
        except Exception as exc:  # the background caller cannot receive this error directly
            errors.append(f"delivery_journal_terminal_failed: {exc}")
            logger.exception(
                "delivery_journal_terminal_failed",
                operation_id=operation_id,
                terminal_state=terminal_state.value,
                error=str(exc),
            )
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
    operation_id: str = "",
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
            kwargs={"skip_index_sync": skip_index_sync, "operation_id": operation_id},
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
