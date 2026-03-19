"""MCP tool registration for build verification.

Registers ``trw_build_check`` and ``trw_quality_dashboard`` on the
FastMCP server instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import ApiFuzzResult, DepAuditResult
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.tools.build._audit import (
    _API_FUZZ_FILE,
    _DEP_AUDIT_FILE,
    _run_api_fuzz,
    _run_dep_audit,
)
from trw_mcp.tools.build._core import (
    _cache_to_context,
    cache_build_status,
    run_build_check,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def register_build_tools(server: FastMCP) -> None:
    """Register build verification tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_build_check(
        scope: str = "full",
        run_path: str | None = None,
        timeout_secs: int | None = None,
        min_coverage: float | None = None,
    ) -> dict[str, object]:
        """Verify your code passes tests and type checking — the gate between implementation and delivery.

        Runs the project's test suite and type checker via subprocess, parses
        results, and caches to .trw/context/build-status.yaml. Returns test
        count, coverage percentage, failure details, and type-check status.
        This is the VALIDATE phase gate — run it after implementation before
        moving to review and delivery.

        Args:
            scope: Check scope — 'full' (tests + type-check), 'pytest', 'mypy'.
                Also supports 'mutations' (mutation testing only),
                'deps' (dependency audit only), 'api' (API fuzz only).
            run_path: Optional run directory for event logging.
            timeout_secs: Override timeout (default: config value, max 600).
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
        project_root = resolve_project_root()
        effective_timeout = min(
            timeout_secs or config.build_check_timeout_secs,
            600,
        )

        # Validate scope
        validation_error = _validate_scope(scope)
        if validation_error:
            return validation_error

        # Handle standalone scopes (mutations, deps, api)
        standalone_result = _handle_standalone_scope(scope, config, project_root, trw_dir)
        if standalone_result:
            return standalone_result

        # --- Standard scopes (pytest/mypy) ---

        logger.info("build_check_started", scope=scope)

        status = run_build_check(
            project_root,
            scope=scope,
            timeout_secs=effective_timeout,
            pytest_args=config.build_check_pytest_args,
            mypy_args=config.build_check_mypy_args,
        )

        cache_path = cache_build_status(trw_dir, status)

        # FIX-035-FR01: Auto-detect active run when not explicitly provided
        from trw_mcp.models.run import Phase
        from trw_mcp.state._paths import find_active_run
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
            duration_secs=status.duration_secs,
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
        _finalize_build_result(result, status, min_coverage)

        # Mark build check result in ceremony state tracker (PRD-CORE-074 FR04)
        try:
            from trw_mcp.state.ceremony_nudge import mark_build_check

            _build_passed = bool(result.get("tests_passed", False))
            mark_build_check(trw_dir, passed=_build_passed)
        except Exception:  # justified: fail-open, ceremony state update must not block build check  # noqa: S110
            logger.debug("build_ceremony_state_update_skipped", exc_info=True)  # justified: fail-open

        # Inject ceremony nudge into response (PRD-CORE-084 FR02)
        try:
            from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName
            from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

            build_passed = bool(result.get("tests_passed", False))
            ctx = NudgeContext(tool_name=ToolName.BUILD_CHECK, build_passed=build_passed)
            append_ceremony_nudge(result, trw_dir, context=ctx)
        except Exception:  # justified: fail-open, nudge injection must not block build check  # noqa: S110
            logger.debug("build_nudge_injection_skipped", exc_info=True)  # justified: fail-open

        # Dep audit on full scope (if enabled)
        if scope == "full" and config.dep_audit_enabled:
            dep_result = _run_dep_audit(project_root, config)
            _cache_to_context(trw_dir, _DEP_AUDIT_FILE, cast("dict[str, object]", dep_result))
            result["dep_audit"] = dep_result
            if not bool(dep_result.get("dep_audit_passed", True)):
                result["dep_audit_blocking"] = True

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


def _validate_scope(scope: str) -> dict[str, object] | None:
    """Validate scope parameter. Returns error dict if invalid, None if valid."""
    valid_scopes = {"full", "pytest", "mypy", "quick", "mutations", "deps", "api"}
    if scope not in valid_scopes:
        return {
            "status": "error",
            "reason": f"Invalid scope '{scope}'. Valid scopes: {sorted(valid_scopes)}",
        }
    return None


def _handle_standalone_scope(
    scope: str,
    config: object,
    project_root: Path,
    trw_dir: Path,
) -> dict[str, object] | None:
    """Handle standalone scopes (mutations, deps, api). Returns result dict or None."""
    from trw_mcp.models.config import TRWConfig

    cfg = cast("TRWConfig", config)

    if scope == "mutations":
        if not cfg.mutation_enabled:
            return {"status": "skipped", "reason": "mutation_enabled is False"}
        from trw_mcp.tools.mutations import cache_mutation_status, run_mutation_check

        mut_result = run_mutation_check(project_root, cfg)
        cache_mutation_status(trw_dir, mut_result)
        return cast("dict[str, object]", mut_result)

    if scope == "deps":
        if not cfg.dep_audit_enabled:
            return {"status": "skipped", "reason": "dep_audit_enabled is False"}
        dep_result: DepAuditResult = _run_dep_audit(project_root, cfg)
        _cache_to_context(trw_dir, _DEP_AUDIT_FILE, cast("dict[str, object]", dep_result))
        return cast("dict[str, object]", dep_result)

    if scope == "api":
        if not cfg.api_fuzz_enabled:
            return {"status": "skipped", "reason": "api_fuzz_enabled is False"}
        fuzz_result: ApiFuzzResult = _run_api_fuzz(project_root, cfg)
        _cache_to_context(trw_dir, _API_FUZZ_FILE, cast("dict[str, object]", fuzz_result))
        return cast("dict[str, object]", fuzz_result)

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
            "mypy_clean": getattr(status, "mypy_clean", False),
            "coverage_pct": str(getattr(status, "coverage_pct", 0)),
            "duration_secs": str(getattr(status, "duration_secs", 0)),
        },
    )


def _finalize_build_result(
    result: dict[str, object],
    status: object,
    min_coverage: float | None,
) -> None:
    """Apply coverage threshold enforcement and enrich result dict."""
    if min_coverage is None:
        return
    coverage_pct = float(getattr(status, "coverage_pct", 0))
    if coverage_pct < min_coverage:
        result["tests_passed"] = False
        result["coverage_threshold_failed"] = True
        result["coverage_threshold"] = min_coverage
        result["coverage_threshold_message"] = (
            f"Coverage {coverage_pct:.1f}% is below required threshold {min_coverage:.1f}%"
        )
