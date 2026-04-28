"""MCP tool registration for build verification.

Registers ``trw_build_check`` on the FastMCP server instance.

PRD-CORE-098: ``trw_build_check`` is a **result reporter** — agents run
tests via Bash and then call this tool to record the outcome for ceremony
tracking and delivery gates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    resolve_pin_key,
    resolve_trw_dir,
)
from trw_mcp.tools.build._core import (
    cache_build_status,
    persist_build_progress_state,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

_BUILD_CHECK_USAGE = (
    "trw_build_check(tests_passed=True, test_count=47, coverage_pct=92.3, mypy_clean=True, scope='full')"
)


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


def _find_active_run_compat(ctx: Context | None) -> Path | None:
    """Call ``find_active_run`` with ctx when supported, else fall back safely.

    Several tests monkeypatch ``find_active_run`` with legacy zero-arg lambdas.
    Production code now prefers the ctx-aware signature, but sprint-close build
    gates still need those patches to work without TypeError.
    """
    try:
        return find_active_run(context=_build_call_context(ctx))
    except TypeError:
        return find_active_run()


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
        mypy_clean: bool = True,
        scope: str = "full",
        failures: list[str] | None = None,
        run_path: str | None = None,
        min_coverage: float | None = None,
    ) -> dict[str, object]:
        """Record build/test results for ceremony tracking and delivery gates.

        Use when:
        - You just ran your test suite (via Bash) and need the outcome logged.
        - You want the delivery gate to see the latest pass/fail + coverage.
        - You want Q-learning feedback attached to a phase transition.

        This tool does NOT execute subprocesses — run tests via Bash first,
        then call this with the results.

        Input:
        - tests_passed: True or False — required; no default guess.
        - test_count: total tests that ran.
        - failure_count: number that failed.
        - coverage_pct: 0.0-100.0.
        - mypy_clean: whether mypy type-checking passed.
        - scope: label like ``full``, ``pytest``, ``mypy``, ``cargo test``.
        - failures: optional list of up to 10 failure descriptions.
        - run_path: optional run directory for event logging.
        - min_coverage: when set, falls tests_passed to False if coverage_pct
          is below the threshold (adds ``coverage_threshold_failed`` flag).

        Output: dict with fields
        {status, run_id?, outcome, tests_passed, coverage_pct, mypy_clean,
         coverage_threshold_failed?, gate_effects: list[str]}.
        """
        reported_tests_passed = _require_tests_passed(tests_passed)
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

        status = BuildStatus(
            tests_passed=reported_tests_passed,
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
        persist_build_progress_state(trw_dir, status, scope=scope)

        # FIX-035-FR01: Auto-detect active run when not explicitly provided
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            # PRD-CORE-141 FR03/FR05: ctx-aware find_active_run.
            resolved_run = _find_active_run_compat(ctx)

        # FIX-035-FR05: Auto-update phase to VALIDATE
        try_update_phase(resolved_run, Phase.VALIDATE)

        # FIX-035-FR02: Log event with proper boolean types
        _log_build_event(resolved_run, scope, status)

        # Q-learning: reward recalled learnings based on build outcome.
        # Process inline so the reported result reflects the persisted Q-update
        # and we never leave a background thread holding the SQLite backend
        # across test/process teardown.
        event_type = "build_passed" if status.tests_passed and status.mypy_clean else "build_failed"
        _process_q_learning(event_type, scope)

        logger.info(
            "build_check_complete",
            scope=scope,
            tests_passed=status.tests_passed,
            mypy_clean=status.mypy_clean,
            coverage_pct=status.coverage_pct,
        )
        if not status.tests_passed or not status.mypy_clean:
            logger.warning(
                "build_check_failed",
                exit_code=1,
                failed_tests=status.failure_count,
            )

        result: dict[str, object] = {
            "tests_passed": status.tests_passed,
            "mypy_clean": status.mypy_clean,
            "timed_out": status.timed_out,
            "coverage_pct": status.coverage_pct,
            "test_count": status.test_count,
            "failure_count": status.failure_count,
            "failures": status.failures,
            "scope": status.scope,
            "duration_secs": status.duration_secs,
            "cache_path": str(cache_path),
        }

        # Coverage threshold enforcement (sprint-finish anti-regression)
        _finalize_build_result(result, min_coverage)

        # Surface Q-learning background health — errors bubble up here
        # so callers see if outcome correlation is failing silently.
        q_health = get_q_learning_health()
        if q_health["last_error"] is not None:
            result["q_learning_error"] = q_health["last_error"]
            result["q_learning_error_count"] = q_health["error_count"]

        return result


# --- Private helpers ---


# ---------------------------------------------------------------------------
# Inline Q-learning outcome processing state.
_q_learning_last_error: str | None = None
_q_learning_error_count: int = 0


def _process_q_learning(event_type: str, scope: str) -> None:
    """Apply Q-learning outcome updates inline and fail open on errors."""
    global _q_learning_last_error, _q_learning_error_count
    try:
        from trw_mcp.scoring import process_outcome_for_event

        updated = process_outcome_for_event(event_type)
        logger.info(
            "q_learning_complete",
            event_type=event_type,
            scope=scope,
            updated_count=len(updated),
        )
        _q_learning_last_error = None
    except Exception as exc:  # justified: fail-open, Q-learning is best-effort
        _q_learning_error_count += 1
        _q_learning_last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.warning(
            "q_learning_failed",
            event_type=event_type,
            scope=scope,
            error_count=_q_learning_error_count,
            exc_info=True,
        )


def get_q_learning_health() -> dict[str, object]:
    """Return Q-learning worker health for observability."""
    return {
        "queue_size": 0,
        "error_count": _q_learning_error_count,
        "last_error": _q_learning_last_error,
        "worker_alive": False,
    }


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
            "mypy_clean": getattr(status, "mypy_clean", False),
            "coverage_pct": str(getattr(status, "coverage_pct", 0)),
            "duration_secs": str(getattr(status, "duration_secs", 0)),
        },
    )


def _finalize_build_result(
    result: dict[str, object],
    min_coverage: float | None,
) -> None:
    """Apply coverage threshold enforcement and enrich result dict."""
    if min_coverage is None:
        return
    coverage_pct = float(str(result.get("coverage_pct", 0)))
    if coverage_pct < min_coverage:
        result["tests_passed"] = False
        result["coverage_threshold_failed"] = True
        result["coverage_threshold"] = min_coverage
        result["coverage_threshold_message"] = (
            f"Coverage {coverage_pct:.1f}% is below required threshold {min_coverage:.1f}%"
        )


def _require_tests_passed(tests_passed: bool | None) -> bool:
    """Require explicit tests_passed reporting with a usage example."""
    if tests_passed is None:
        raise ValueError(
            f"tests_passed is required. Report the outcome after running tests via Bash. Example: {_BUILD_CHECK_USAGE}"
        )
    return tests_passed
