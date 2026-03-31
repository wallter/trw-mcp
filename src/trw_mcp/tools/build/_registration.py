"""MCP tool registration for build verification.

Registers ``trw_build_check`` and ``trw_quality_dashboard`` on the
FastMCP server instance.

PRD-CORE-098: ``trw_build_check`` is a **result reporter** — agents run
tests via Bash and then call this tool to record the outcome for ceremony
tracking and delivery gates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import structlog
from fastmcp import FastMCP

from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import find_active_run, resolve_trw_dir
from trw_mcp.tools.build._core import (
    cache_build_status,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def register_build_tools(server: FastMCP) -> None:
    """Register build verification tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_build_check(
        tests_passed: bool,
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

        Run your tests via Bash first, then report results here. This tool
        records the outcome for ceremony scoring, phase gates, and Q-learning
        feedback. It does NOT execute subprocesses itself.

        Args:
            tests_passed: Whether all tests passed. **Required.**
            test_count: Total number of tests that ran.
            failure_count: Number of failed tests.
            coverage_pct: Coverage percentage (0.0-100.0).
            mypy_clean: Whether mypy type checking passed.
            scope: Freeform label for what was checked — e.g. 'full', 'pytest',
                'mypy', 'cargo test', 'npm test'. Used for event logging only.
            failures: Optional list of failure descriptions (up to 10).
            run_path: Optional run directory for event logging.
            min_coverage: Optional minimum coverage percentage. If set and
                coverage falls below this threshold, tests_passed is set to
                False and a coverage_threshold_failed flag is added to the result.
        """
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
            tests_passed=tests_passed,
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

        # FIX-035-FR01: Auto-detect active run when not explicitly provided
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        # FIX-035-FR05: Auto-update phase to VALIDATE
        try_update_phase(resolved_run, Phase.VALIDATE)

        # FIX-035-FR02: Log event with proper boolean types
        _log_build_event(resolved_run, scope, status)

        # Q-learning: reward recalled learnings based on build outcome
        try:
            from trw_mcp.scoring import process_outcome_for_event

            event_type = "build_passed" if status.tests_passed and status.mypy_clean else "build_failed"
            process_outcome_for_event(event_type)
        except Exception:  # justified: fail-open, Q-learning is best-effort, never blocks build check
            logger.debug("q_learning_reward_failed", exc_info=True)

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

        # Mark build check result in ceremony state tracker (PRD-CORE-074 FR04)
        try:
            from trw_mcp.state.ceremony_nudge import mark_build_check

            _build_passed = bool(result.get("tests_passed", False))
            mark_build_check(trw_dir, passed=_build_passed)
        except Exception:  # justified: fail-open, ceremony state update must not block build check
            logger.debug("build_ceremony_state_update_skipped", exc_info=True)  # justified: fail-open

        # Ceremony nudge injection
        try:
            from trw_mcp.state._nudge_state import NudgeContext, ToolName
            from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

            ctx = NudgeContext(
                tool_name=ToolName.BUILD_CHECK,
                build_passed=bool(result.get("tests_passed", False)),
            )
            append_ceremony_nudge(result, trw_dir, context=ctx)
        except Exception:  # justified: fail-open
            pass

        return result

    @server.tool()
    @log_tool_call
    def trw_quality_dashboard(
        window_days: int = 90,
        compare_sprint: str = "",
        format: str = "summary",
    ) -> dict[str, object]:
        """View quality trends — ceremony scores, coverage, review verdicts, and degradation alerts.

        Aggregates session event data to show how your project's quality metrics
        are trending over time. Use compare_sprint to see sprint-over-sprint deltas.

        Args:
            window_days: Number of days to include (1-365, default 90).
            compare_sprint: Optional sprint ID to compare against previous sprint.
            format: Output format — "summary" or "detailed".
        """
        from trw_mcp.state.dashboard import aggregate_dashboard

        trw_dir = resolve_trw_dir()
        clamped_days = max(1, min(365, window_days))
        return aggregate_dashboard(trw_dir, clamped_days, compare_sprint)


# --- Private helpers ---



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
    coverage_pct = float(result.get("coverage_pct", 0))
    if coverage_pct < min_coverage:
        result["tests_passed"] = False
        result["coverage_threshold_failed"] = True
        result["coverage_threshold"] = min_coverage
        result["coverage_threshold_message"] = (
            f"Coverage {coverage_pct:.1f}% is below required threshold {min_coverage:.1f}%"
        )
