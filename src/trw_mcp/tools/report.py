"""Post-run and cross-run analytics report tools — PRD-CORE-030, PRD-CORE-031.

Exposes trw_run_report (single-run) and trw_analytics_report (cross-run)
as MCP tools that generate structured analytics.
"""

from __future__ import annotations

from typing import cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.typed_dicts import AnalyticsReport, RunReportResultDict
from trw_mcp.state._paths import (
    TRWCallContext,
    resolve_pin_key,
    resolve_run_path,
    resolve_trw_dir,
)
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.report import assemble_report
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def _build_call_context(ctx: Context | None) -> TRWCallContext:
    """Construct a :class:`TRWCallContext` for pin-state helpers (PRD-CORE-141 FR03)."""
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def register_report_tools(server: FastMCP) -> None:
    """Register post-run analytics tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_run_report(
        ctx: Context | None = None,
        run_path: str | None = None,
    ) -> RunReportResultDict:
        """See what happened in a run — phase timing, event counts, learning yield, and build results.

        Reads run.yaml, events.jsonl, checkpoints.jsonl, and build-status.yaml to
        produce aggregated metrics. Use this to understand a completed run's outcomes
        or to diagnose issues in an active run.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        try:
            # PRD-CORE-141 FR03/FR05: ctx-aware path resolution.
            resolved_path = resolve_run_path(run_path, context=_build_call_context(ctx))
        except StateError as exc:
            return {"error": str(exc), "status": "failed"}

        try:
            trw_dir = resolve_trw_dir()
        except Exception:  # justified: fail-open, trw_dir resolution falls back to relative path
            trw_dir = resolved_path.parent.parent.parent / ".trw"

        try:
            reader = FileStateReader()
            report = assemble_report(resolved_path, reader, trw_dir)
            logger.info("trw_run_report_generated", run_id=report.run_id)
            result: RunReportResultDict = cast("RunReportResultDict", report.model_dump())
            return result
        except StateError as exc:
            return {"error": str(exc), "status": "failed"}

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_analytics_report(since: str | None = None) -> AnalyticsReport:
        """See trends across all runs — build pass rate, ceremony compliance, and whether process is improving.

        Scans all run directories, computes per-run ceremony scores (0-100), and
        returns aggregate metrics with trend analysis. Use this to identify systemic
        issues like declining test coverage or ceremony drift.

        Args:
            since: Optional ISO date filter (YYYY-MM-DD). Only runs started on or after this date are included.
        """
        from trw_mcp.state.analytics.report import scan_all_runs

        try:
            report: AnalyticsReport = scan_all_runs(since=since)
            logger.info(
                "trw_analytics_report_generated",
                runs_scanned=report.get("runs_scanned", 0),
            )
            return report
        except Exception as exc:  # justified: boundary, scan_all_runs reads many run dirs
            return cast("AnalyticsReport", {"error": str(exc), "status": "failed"})
