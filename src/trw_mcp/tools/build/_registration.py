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

import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.models._evidence_plans import BuildCommandResult
from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    resolve_pin_key,
    resolve_trw_dir,
)
from trw_mcp.tools._evidence_persistence import WriteOutcome
from trw_mcp.tools.build._build_check_helpers import (
    _finalize_build_result as _finalize_build_result,
)
from trw_mcp.tools.build._build_check_helpers import (
    _require_tests_passed as _require_tests_passed,
)
from trw_mcp.tools.build._build_check_helpers import (
    derive_duration_secs as derive_duration_secs,
)
from trw_mcp.tools.build._build_check_helpers import (
    derive_test_count as derive_test_count,
)
from trw_mcp.tools.build._build_check_helpers import (
    reconcile_typed_results as reconcile_typed_results,
)
from trw_mcp.tools.build._core import (
    cache_build_status,
    persist_build_progress_state,
)
from trw_mcp.tools.build._failure_attribution import attribute_failures

# PRD-FIX-088 FR01: the deferred Q-learning subsystem lives in a sibling module.
# Re-exported explicitly (``X as X``) because mypy --strict rejects implicit
# re-export and ``test_fix027_scoring_build_check.py`` reaches for
# ``reg_mod.get_q_learning_health()``.
from trw_mcp.tools.build._q_learning_dispatch import (
    get_q_learning_health as get_q_learning_health,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


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
        command_results: list[dict[str, object]] | str | None = None,
    ) -> dict[str, object]:
        """Record build/test results for ceremony tracking and delivery gates.

        Use when you just ran validation and need it logged for the delivery gate.

        This tool does not execute anything - run validation yourself, then
        call this with the results. tests_passed is required with no default
        guess. scope is e.g. "full" or "quick". min_coverage flips
        tests_passed False below threshold.

        Output: tests_passed, static_checks_clean, coverage_pct,
        coverage_threshold_failed.

        Args:
            coverage_pct: 0.0-100.0, if measured.
            static_checks_clean: pass/fail for static/type/lint checks. Set this
                one; omitting it records CLEAN. mypy_clean is its legacy alias.
            command_results: enforce mode requires one entry per required command,
                each {"command_id": "tests"|"static_checks", "label": str,
                "command_class": "test"|"static", "exit_code": int}.
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
            reported_tests_passed, effective_static_checks_clean = reconcile_typed_results(
                typed_command_results,
                tests_passed=tests_passed,
                static_checks_clean=static_checks_clean,
            )
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

        # This tool executes nothing, so it has no clock of its own. A real
        # duration exists only when the caller supplied typed command results
        # carrying started_at/completed_at; otherwise it stays unknown and is
        # omitted from the response and the event rather than reported as 0.0.
        observed_duration_secs = derive_duration_secs(typed_command_results)

        # WD-01: the deliver gate refuses a "pass" that recorded zero tests, so
        # the count a typed command result already carries must reach the record
        # rather than being lost to the flat argument's default of 0.
        effective_test_count = derive_test_count(typed_command_results, reported=test_count)

        # Step: persist (cache + progress state)
        _persist_started = monotonic()
        status = BuildStatus(
            tests_passed=reported_tests_passed,
            static_checks_clean=effective_static_checks_clean,
            mypy_clean=mypy_clean,
            timed_out=False,
            coverage_pct=coverage_pct,
            test_count=effective_test_count,
            failure_count=failure_count,
            failures=effective_failures,
            timestamp=datetime.now(timezone.utc).isoformat(),
            scope=scope,
            duration_secs=observed_duration_secs,
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

        # R10: a run-level build result is not evidence that recently exposed
        # learnings were applied or useful. Record the build, not inferred Q credit.

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
            "cache_path": str(cache_path),
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

        # Ledger UF-042: trw_build_check never called the ceremony injector, so
        # ``NudgeContext.build_passed`` had NO production writer anywhere and the
        # "Build failed -> revert to PLAN" branch of ``_reversion_prompt`` plus
        # the context-pool bypass in ``_select_nudge_pool`` were unreachable for
        # that reason alone. This is the writer.
        #
        # Fail-open, matching trw_deliver's call site
        # (``_ceremony_deliver_tool._append_deliver_ceremony_status``): nudge
        # injection is telemetry riding the build hot path, and a filesystem
        # error, a lock contention, or a serialization failure inside it must
        # never turn a completed build check into a tool failure.
        try:
            from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool

            _build_ok = status.tests_passed and effective_static_checks_clean
            append_ceremony_status_for_tool(
                result, trw_dir, tool_name="build_check", tool_success=_build_ok, build_passed=_build_ok
            )
        except Exception:  # justified: fail-open, status decoration must not fail build_check
            logger.debug("build_check_ceremony_status_skipped", exc_info=True)

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
            # WD-01: the deliver-time build gate needs the test count to reject a
            # "pass" that ran zero tests. It was absent from this payload, so the
            # gate had no way to tell a real run from a self-reported one.
            "test_count": int(getattr(status, "test_count", 0) or 0),
            "tests_passed": getattr(status, "tests_passed", False),
            "static_checks_clean": getattr(
                status,
                "static_checks_clean",
                getattr(status, "mypy_clean", False),
            ),
            "mypy_clean": getattr(status, "mypy_clean", False),
            "coverage_pct": str(getattr(status, "coverage_pct", 0)),
        },
    )
