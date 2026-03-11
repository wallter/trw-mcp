"""MCP tool registration for build verification.

Registers ``trw_build_check`` and ``trw_quality_dashboard`` on the
FastMCP server instance.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.tools.build._audit import (
    _DEP_AUDIT_FILE,
    _API_FUZZ_FILE,
    _run_api_fuzz,
    _run_dep_audit,
)
from trw_mcp.tools.build._core import (
    _cache_to_context,
    cache_build_status,
    run_build_check,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_config = get_config()


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
        if not _config.build_check_enabled:
            return {
                "status": "skipped",
                "reason": "build_check_enabled is False",
            }

        trw_dir = resolve_trw_dir()
        project_root = resolve_project_root()
        effective_timeout = min(
            timeout_secs or _config.build_check_timeout_secs,
            600,
        )

        _VALID_SCOPES = {"full", "pytest", "mypy", "quick", "mutations", "deps", "api"}
        if scope not in _VALID_SCOPES:
            return {
                "status": "error",
                "reason": f"Invalid scope '{scope}'. Valid scopes: {sorted(_VALID_SCOPES)}",
            }

        # --- Standalone scopes (no pytest/mypy) ---

        if scope == "mutations":
            if not _config.mutation_enabled:
                return {"status": "skipped", "reason": "mutation_enabled is False"}
            from trw_mcp.tools.mutations import (
                cache_mutation_status,
                run_mutation_check,
            )

            mut_result = run_mutation_check(project_root, _config)
            cache_mutation_status(trw_dir, mut_result)
            return mut_result

        if scope == "deps":
            if not _config.dep_audit_enabled:
                return {"status": "skipped", "reason": "dep_audit_enabled is False"}
            dep_result = _run_dep_audit(project_root, _config)
            _cache_to_context(trw_dir, _DEP_AUDIT_FILE, dep_result)
            return dep_result

        if scope == "api":
            if not _config.api_fuzz_enabled:
                return {"status": "skipped", "reason": "api_fuzz_enabled is False"}
            fuzz_result = _run_api_fuzz(project_root, _config)
            _cache_to_context(trw_dir, _API_FUZZ_FILE, fuzz_result)
            return fuzz_result

        # --- Standard scopes (pytest/mypy) ---

        status = run_build_check(
            project_root,
            scope=scope,
            timeout_secs=effective_timeout,
            pytest_args=_config.build_check_pytest_args,
            mypy_args=_config.build_check_mypy_args,
        )

        cache_path = cache_build_status(trw_dir, status)

        # FIX-035-FR01: Auto-detect active run when not explicitly provided
        from trw_mcp.state._paths import find_active_run

        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        # FIX-035-FR05: Auto-update phase to VALIDATE
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.VALIDATE)

        # FIX-035-FR02: Log event with proper boolean types
        if resolved_run is not None:
            from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

            events_path = resolved_run / "meta" / "events.jsonl"
            if events_path.parent.exists():
                event_logger = FileEventLogger(FileStateWriter())
                event_logger.log_event(events_path, "build_check_complete", {
                    "scope": scope,
                    "tests_passed": status.tests_passed,
                    "mypy_clean": status.mypy_clean,
                    "coverage_pct": str(status.coverage_pct),
                    "duration_secs": str(status.duration_secs),
                })

        # Q-learning: reward recalled learnings based on build outcome
        try:
            from trw_mcp.scoring import process_outcome_for_event
            event_type = "build_passed" if status.tests_passed and status.mypy_clean else "build_failed"
            process_outcome_for_event(event_type)
        except Exception:
            pass  # Q-learning is best-effort, never block build check

        logger.info(
            "build_check_complete",
            scope=scope,
            tests_passed=status.tests_passed,
            mypy_clean=status.mypy_clean,
            coverage_pct=status.coverage_pct,
            duration_secs=status.duration_secs,
        )

        result: dict[str, object] = {
            "tests_passed": status.tests_passed,
            "mypy_clean": status.mypy_clean,
            "coverage_pct": status.coverage_pct,
            "test_count": status.test_count,
            "failure_count": status.failure_count,
            "failures": status.failures,
            "scope": status.scope,
            "duration_secs": status.duration_secs,
            "cache_path": str(cache_path),
        }

        # Coverage threshold enforcement (sprint-finish anti-regression)
        if min_coverage is not None and status.coverage_pct < min_coverage:
            result["tests_passed"] = False
            result["coverage_threshold_failed"] = True
            result["coverage_threshold"] = min_coverage
            result["coverage_threshold_message"] = (
                f"Coverage {status.coverage_pct:.1f}% is below "
                f"required threshold {min_coverage:.1f}%"
            )

        # Dep audit on full scope (if enabled)
        if scope == "full" and _config.dep_audit_enabled:
            dep_result = _run_dep_audit(project_root, _config)
            _cache_to_context(trw_dir, _DEP_AUDIT_FILE, dep_result)
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
