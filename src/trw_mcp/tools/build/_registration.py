"""MCP tool registration for build verification.

Registers ``trw_build_check`` on the FastMCP server instance.

PRD-CORE-098: ``trw_build_check`` is a **result reporter** — agents run
tests via Bash and then call this tool to record the outcome for ceremony
tracking and delivery gates.

PRD-FIX-088 FR01: Q-learning outcome correlation is ALWAYS deferred to a
dedicated background worker thread (single-flight + coalescing queue).
Pre-fix the inline path could take >90 s on large corpora, holding the
MCP response on the SSE stream for the entire duration.

PRD-FIX-088 FR03: Per-step ``step_durations_ms`` telemetry mirrors the
PRD-FIX-084 precedent on ``trw_session_start``.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from time import monotonic
from typing import Literal

import structlog
from fastmcp import Context, FastMCP

import trw_mcp.tools._q_learning_state as _qls
from trw_mcp.models._evidence_plans import BuildCommandResult
from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts._tools import (
    QLearningDeferredDict,
    QLearningHealthDict,
)
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    resolve_pin_key,
    resolve_trw_dir,
)
from trw_mcp.tools._evidence_persistence import WriteOutcome
from trw_mcp.tools.build._build_check_helpers import (
    _BUILD_CHECK_USAGE as _BUILD_CHECK_USAGE,
)
from trw_mcp.tools.build._build_check_helpers import (
    _finalize_build_result as _finalize_build_result,
)
from trw_mcp.tools.build._build_check_helpers import (
    _require_tests_passed as _require_tests_passed,
)
from trw_mcp.tools.build._core import (
    cache_build_status,
    persist_build_progress_state,
)
from trw_mcp.tools.build._failure_attribution import attribute_failures
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

# PRD-FIX-088 FR01: literal alias for the ``thread_state`` field on
# :class:`QLearningDeferredDict`. Kept private to this module since it
# is an implementation detail of the dispatcher.
_DispatchThreadState = Literal["launched", "queued", "queue_full"]


def _build_call_context(ctx: Context | None) -> TRWCallContext:
    """Construct a :class:`TRWCallContext` for pin-state helpers (PRD-CORE-141 FR03)."""
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    try:
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    except Exception:
        raw_session = None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def register_build_tools(server: FastMCP) -> None:
    """Register build verification tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_build_check(
        ctx: Context | None = None,
        tests_passed: bool | None = None,
        test_count: int = 0,
        failure_count: int = 0,
        coverage_pct: float = 0.0,
        static_checks_clean: bool | None = None,
        mypy_clean: bool = True,
        scope: str = "full",
        failures: list[str] | None = None,
        run_path: str | None = None,
        min_coverage: float | None = None,
        command_results: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Record build/test results for ceremony tracking and delivery gates.

        Use when:
        - You just ran project-native validation (via shell/CI/script) and need the outcome logged.
        - You want the delivery gate to see the latest pass/fail + coverage.
        - You want Q-learning feedback attached to a phase transition.

        This tool does NOT execute subprocesses — run validation commands first,
        then call this with the results.

        Input:
        - tests_passed: True or False — required; no default guess.
        - test_count: total checks/tests that ran.
        - failure_count: number that failed.
        - coverage_pct: 0.0-100.0, if measured.
        - static_checks_clean: preferred neutral status for configured static/type/lint/schema checks.
        - mypy_clean: legacy compatibility alias; use only for older clients or Python-specific reports.
        - scope: label like ``full``, ``quick``, ``type-check``, ``cargo test``, ``npm test``.
        - failures: optional list of up to 10 failure descriptions.
        - run_path: optional run directory for event logging.
        - min_coverage: when set, falls tests_passed to False if coverage_pct
          is below the threshold (adds ``coverage_threshold_failed`` flag).
        - command_results: typed results for the server-required ``tests`` and
          ``static_checks`` command IDs. Required in evidence enforce mode;
          legacy booleans are accepted only by observe mode.

        Output: dict with fields
        {status, run_id?, outcome, tests_passed, coverage_pct,
         static_checks_clean, mypy_clean, coverage_threshold_failed?,
         gate_effects: list[str]}.
        """
        # PRD-FIX-088 FR03: Per-step latency telemetry for ``trw_build_check``.
        # Every named step records elapsed-since-start so future regressions
        # of the "step accidentally O(corpus)" class are visible from one
        # log line.
        _call_started_at = monotonic()
        step_durations_ms: dict[str, float] = {}

        def _record_step(step_key: str, started_at: float) -> None:
            step_durations_ms[step_key] = round((monotonic() - started_at) * 1000.0, 2)

        # PRD-FIX-088 FR01: ``tool_call_id`` is captured up-front and
        # threaded through the bg worker so async ``q_learning_complete``
        # and ``outcome_correlation_applied`` events correlate back to the
        # originating call. ``log_tool_call`` already binds the same id
        # into structlog contextvars; we pull it from there when present
        # to keep ids consistent, otherwise mint a fresh 12-char hex.
        bound_ctx = structlog.contextvars.get_contextvars()
        bound_id = bound_ctx.get("tool_call_id")
        tool_call_id: str = bound_id if isinstance(bound_id, str) and bound_id else uuid.uuid4().hex[:12]

        from trw_mcp.tools._evidence_writers import parse_build_command_results

        typed_command_results = parse_build_command_results(command_results)
        if typed_command_results is None:
            reported_tests_passed = _require_tests_passed(tests_passed)
            effective_static_checks_clean = mypy_clean if static_checks_clean is None else static_checks_clean
        else:
            by_id = {item.command_id: item for item in typed_command_results}
            typed_tests_passed = by_id.get("tests") is not None and by_id["tests"].passed
            typed_static_clean = by_id.get("static_checks") is not None and by_id["static_checks"].passed
            if tests_passed is not None and tests_passed != typed_tests_passed:
                raise ValueError("tests_passed contradicts typed command result 'tests'")
            if static_checks_clean is not None and static_checks_clean != typed_static_clean:
                raise ValueError("static_checks_clean contradicts typed command result 'static_checks'")
            reported_tests_passed = typed_tests_passed
            effective_static_checks_clean = typed_static_clean
        config = get_config()
        if not config.build_check_enabled:
            return {
                "status": "skipped",
                "reason": "build_check_enabled is False",
            }

        trw_dir = resolve_trw_dir()

        # --- Build status from reported params ---

        logger.info("build_check_started", scope=scope)

        effective_failures = (failures or [])[:10]

        # Step: persist (cache + progress state)
        _persist_started = monotonic()
        status = BuildStatus(
            tests_passed=reported_tests_passed,
            static_checks_clean=effective_static_checks_clean,
            mypy_clean=mypy_clean,
            timed_out=False,
            coverage_pct=coverage_pct,
            test_count=test_count,
            failure_count=failure_count,
            failures=effective_failures,
            timestamp=datetime.now(timezone.utc).isoformat(),
            scope=scope,
            duration_secs=0.0,
        )

        cache_path = cache_build_status(trw_dir, status)

        # PRD-FIX-077-FR01: persist build outcome for delivery-gate fallback.
        persist_build_progress_state(
            trw_dir,
            status,
            scope=scope,
            session_id=resolve_pin_key(ctx=ctx, explicit=None),
        )
        _record_step("persist", _persist_started)

        # Step: run_resolve + phase update
        _run_resolve_started = monotonic()
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            # PRD-CORE-141 FR03/FR05: ctx-aware find_active_run.
            resolved_run = find_active_run(context=_build_call_context(ctx))

        try_update_phase(resolved_run, Phase.VALIDATE)

        # PRD-CORE-205-FR04: dual-write a per-run content-bound BuildReceipt in
        # observe mode. Receipts are keyed per-run beneath meta/receipts/ so a
        # concurrent session cannot overwrite this run's proof (the 88c669bf4
        # global build-status incident). Strictly fail-open — a scope/binding
        # problem skips the receipt and leaves the legacy projection intact.
        receipt_write = _dual_write_build_receipt(
            resolved_run,
            status,
            scope,
            effective_static_checks_clean,
            coverage_pct,
            typed_command_results,
            min_coverage,
        )
        _record_step("run_resolve", _run_resolve_started)

        # Step: log_event
        _log_event_started = monotonic()
        _log_build_event(resolved_run, scope, status)
        _record_step("log_event", _log_event_started)

        # Step: q_learning_dispatch — always defer to a background worker.
        # PRD-FIX-088 FR01: pre-fix this ran inline and could take >90 s on
        # large corpora; the response was held on the SSE stream the whole
        # time. Now it returns immediately with ``q_learning_deferred`` set.
        _q_dispatch_started = monotonic()
        event_type = "build_passed" if status.tests_passed and effective_static_checks_clean else "build_failed"
        q_learning_deferred = _dispatch_q_learning_async(event_type, scope, tool_call_id)
        _record_step("q_learning_dispatch", _q_dispatch_started)

        # Step: finalize (result-dict assembly)
        _finalize_started = monotonic()
        if not status.tests_passed or not effective_static_checks_clean:
            logger.warning(
                "build_check_failed",
                exit_code=1,
                failed_tests=status.failure_count,
            )

        result: dict[str, object] = {
            "tests_passed": status.tests_passed,
            "static_checks_clean": effective_static_checks_clean,
            "mypy_clean": status.mypy_clean,
            "timed_out": status.timed_out,
            "coverage_pct": status.coverage_pct,
            "test_count": status.test_count,
            "failure_count": status.failure_count,
            "failures": status.failures,
            "scope": status.scope,
            "duration_secs": status.duration_secs,
            "cache_path": str(cache_path),
            "q_learning_deferred": q_learning_deferred,
            "build_receipt_id": receipt_write.receipt_id if receipt_write is not None else "",
            "typed_receipt_state": "written" if receipt_write is not None and receipt_write.ok else "missing",
            "typed_receipt_reason": receipt_write.reason_code if receipt_write is not None else "receipt_not_written",
        }

        # PRD-IMPROVE-MCP-02 FR1: triage each reported failure as
        # likely-yours vs pre-existing on this working tree, so the agent
        # skips git archaeology. Fail-open inside ``attribute_failures``;
        # only runs when failures were reported.
        attribution = attribute_failures(effective_failures)
        if attribution is not None:
            result["failure_attribution"] = attribution
            result["summary"] = attribution["summary"]

        # Coverage threshold enforcement (sprint-finish anti-regression)
        _finalize_build_result(result, min_coverage)

        # Surface Q-learning background health — errors bubble up here
        # so callers see if outcome correlation is failing silently.
        q_health = get_q_learning_health()
        if q_health["last_error"] is not None:
            result["q_learning_error"] = q_health["last_error"]
            result["q_learning_error_count"] = q_health["error_count"]

        _record_step("finalize", _finalize_started)
        _record_step("total", _call_started_at)
        result["step_durations_ms"] = step_durations_ms

        # PRD-FIX-088 FR03 acceptance #2: ``step_durations_ms`` MUST appear
        # on the ``build_check_complete`` log payload AND on the result
        # dict. Emitted AFTER ``_record_step("total", ...)`` so the dict
        # is fully populated; pre-fix the log fired before ``finalize``
        # and ``total`` were recorded, yielding an incomplete mirror.
        logger.info(
            "build_check_complete",
            scope=scope,
            tests_passed=status.tests_passed,
            static_checks_clean=effective_static_checks_clean,
            mypy_clean=status.mypy_clean,
            coverage_pct=status.coverage_pct,
            step_durations_ms=step_durations_ms,
            tool_call_id=tool_call_id,
        )

        return result


# --- Private helpers ---


# ---------------------------------------------------------------------------
# PRD-FIX-088 FR01: Background Q-learning worker — single-flight + queue.
# Worker handle, lock, and aggregated health counters live in
# ``_q_learning_state`` (extracted to keep this module testable; tests
# reset state via the conftest fixture).


def _dispatch_q_learning_async(
    event_type: str,
    scope: str,
    tool_call_id: str,
) -> QLearningDeferredDict:
    """Schedule Q-learning outcome correlation on the background worker.

    Returns a stable :class:`QLearningDeferredDict` (always non-None) with
    a literal ``reason`` and a literal ``thread_state`` so log readers and
    tests get static-typed access without ``cast`` / ``# type: ignore``.

    PRD-FIX-088 FR01: ``tool_call_id`` is threaded through so the async
    ``q_learning_complete`` and ``outcome_correlation_applied`` events the
    worker emits can be correlated back to the originating tool call.
    """
    scheduled_at = datetime.now(timezone.utc).isoformat()
    thread_state: _DispatchThreadState
    with _qls._q_lock:
        worker_alive = _qls._q_thread is not None and _qls._q_thread.is_alive()
        if worker_alive:
            try:
                _qls._q_queue.put_nowait((event_type, tool_call_id))
                thread_state = "queued"
            except Exception:  # justified: bounded queue overflow is best-effort
                logger.warning(
                    "q_learning_queue_full",
                    event_type=event_type,
                    scope=scope,
                    queue_max=_qls._q_queue.maxsize,
                    tool_call_id=tool_call_id,
                )
                thread_state = "queue_full"
        else:
            _qls._q_thread = threading.Thread(
                target=_q_learning_worker,
                args=(event_type, scope, tool_call_id),
                name="trw-q-learning",
                daemon=True,
            )
            _qls._q_thread.start()
            thread_state = "launched"
    return QLearningDeferredDict(
        reason="deferred_always",
        scheduled_at=scheduled_at,
        thread_state=thread_state,
        tool_call_id=tool_call_id,
    )


def _q_learning_worker(
    initial_event_type: str,
    scope: str,
    tool_call_id: str,
) -> None:
    """Background worker: process the initial event then drain the coalescing queue.

    Single-flight contract: only one worker at a time. While alive, peer
    callers enqueue ``(event_type, tool_call_id)`` onto ``_q_queue``;
    after the initial pass completes, the worker drains the queue and
    exits. The handle is cleared in ``finally`` so a crash leaves no
    zombie reference.

    PRD-FIX-088 P1.5 Fix 6: catches ``Exception`` (not ``BaseException``)
    so daemon threads do not swallow ``KeyboardInterrupt``/``SystemExit``.
    P1.5 Fix 8: this is the **single** crash-recording site — the inner
    helper now raises straight through so ``q_learning_worker_crashed``
    is the one accurate event when correlation throws.
    """
    try:
        _process_q_learning_inline(initial_event_type, scope, tool_call_id)
        # Drain coalescing queue until empty. ``get_nowait`` returns
        # immediately on empty, breaking the loop.
        while True:
            try:
                queued_event, queued_call_id = _qls._q_queue.get_nowait()
            except Empty:
                break
            _process_q_learning_inline(queued_event, scope, queued_call_id)
    except Exception as exc:  # justified: bg-thread last-resort barrier
        # PRD-FIX-088 round-2 F2: atomic count + last_error update via
        # ``_q_lock``-guarded helper; prevents torn reads from
        # ``get_q_learning_health()``.
        new_count = _qls.record_error(exc)
        logger.exception(
            "q_learning_worker_crashed",
            event_type=initial_event_type,
            scope=scope,
            error_count=new_count,
            tool_call_id=tool_call_id,
        )
    finally:
        with _qls._q_lock:
            _qls._q_thread = None


def _process_q_learning_inline(
    event_type: str,
    scope: str,
    tool_call_id: str,
) -> None:
    """Run a single Q-learning correlation pass and record outcome.

    PRD-FIX-088 P1.5 Fix 8: previously this caught ``Exception`` and
    logged ``q_learning_failed``, which made the worker's outer
    ``except`` unreachable for normal failures and produced two
    overlapping error events. The catch has been removed; exceptions
    propagate to the worker and are recorded once via
    ``q_learning_worker_crashed``.

    Note (Fix 10): the import of ``process_outcome_for_event`` is
    deferred here to avoid a potential ``trw_mcp.scoring`` ↔ ``tools``
    import cycle at module-load time. The function is called once per
    pass, so the per-call import cost is negligible.
    """
    from trw_mcp.scoring import process_outcome_for_event

    updated = process_outcome_for_event(
        event_type,
        tool_call_id=tool_call_id,
    )
    logger.info(
        "q_learning_complete",
        event_type=event_type,
        scope=scope,
        updated_count=len(updated),
        tool_call_id=tool_call_id,
    )
    # PRD-FIX-088 round-2 F2: lock-guarded clear via helper.
    _qls.mark_success()


def get_q_learning_health() -> QLearningHealthDict:
    """Return Q-learning worker health for observability.

    Round-2 F2: ``snapshot()`` returns ``(count, last_error)`` as a
    coherent pair under ``_q_lock`` so callers never see a newer count
    paired with a stale message.
    """
    worker_alive = _qls._q_thread is not None and _qls._q_thread.is_alive()
    error_count, last_error = _qls.snapshot()
    return QLearningHealthDict(
        queue_size=_qls._q_queue.qsize(),
        error_count=error_count,
        last_error=last_error,
        worker_alive=worker_alive,
    )


def _dual_write_build_receipt(
    resolved_run: Path | None,
    status: BuildStatus,
    scope: str,
    static_checks_clean: bool,
    coverage_pct: float,
    command_results: tuple[BuildCommandResult, ...] | None,
    coverage_threshold: float | None,
) -> WriteOutcome | None:
    """PRD-CORE-205-FR04 BuildReceipt write; failure is missing evidence."""
    if resolved_run is None:
        return None
    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.tools._evidence_writers import record_build_receipt

        mode = str(getattr(get_config(), "evidence_receipt_mode", "observe"))
        return record_build_receipt(
            resolved_run,
            resolve_project_root(),
            tests_passed=status.tests_passed,
            static_checks_clean=static_checks_clean,
            scope_label=scope,
            coverage_pct=coverage_pct if coverage_pct > 0 else None,
            policy_mode=mode,
            command_results=command_results,
            coverage_threshold=coverage_threshold,
        )
    except Exception:  # justified: dual-write must never break the reporter tool
        logger.warning("build_receipt_dual_write_skipped", exc_info=True)
        return None


def _log_build_event(resolved_run: Path | None, scope: str, status: object) -> None:
    """Log build_check_complete event to run's events.jsonl."""
    if resolved_run is None:
        return
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

    events_path = resolved_run / "meta" / "events.jsonl"
    if not events_path.parent.exists():
        return
    event_logger = FileEventLogger(FileStateWriter())
    event_logger.log_event(
        events_path,
        "build_check_complete",
        {
            "scope": scope,
            "tests_passed": getattr(status, "tests_passed", False),
            "static_checks_clean": getattr(
                status,
                "static_checks_clean",
                getattr(status, "mypy_clean", False),
            ),
            "mypy_clean": getattr(status, "mypy_clean", False),
            "coverage_pct": str(getattr(status, "coverage_pct", 0)),
            "duration_secs": str(getattr(status, "duration_secs", 0)),
        },
    )
