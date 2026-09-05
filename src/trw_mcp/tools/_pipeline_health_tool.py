"""MCP tool registration for trw_pipeline_health — PRD-FIX-COMPOUNDING-6 FR02.

Exposes the five compounding-pipeline probes as an operator/agent-callable
MCP tool. Kept in a separate file so server/_tools.py imports are lean and
_pipeline_health.py stays under the 350-LOC gate.
"""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

from trw_mcp.tools._pipeline_health import PipelineHealthResult, step_pipeline_health

logger = structlog.get_logger(__name__)


def register_pipeline_health_tools(server: FastMCP) -> None:
    """Register trw_pipeline_health on the MCP server."""

    @server.tool(output_schema=None)
    def trw_pipeline_health() -> PipelineHealthResult:
        """Probe 5 compounding-pipeline signals; report the degraded ones.

        Use when: trw_session_start returned a pipeline_health_advisory, or
        for a routine health check. Read-only, fail-open per signal.
        """
        # Signals: sync_push, graph_edges, embedding_coverage,
        # recall_feedback, bandit_state. Thresholds: sync_push =
        # consecutive_failures + last_push_at age; graph_edges = knowledge
        # graph empty; embedding_coverage < 10%; recall_feedback = all
        # recall_count == 0; bandit_state = mtime stale.
        try:
            from trw_mcp.state._paths import resolve_trw_dir

            trw_dir = resolve_trw_dir()
            return step_pipeline_health(trw_dir)
        except Exception as exc:  # justified: fail-open, tool must never crash
            logger.warning("pipeline_health_tool_failed", error=str(exc))
            # DEF-06: this used to omit ``measured`` entirely — top-level AND
            # per-signal — so a tool crash rendered identically to a healthy
            # aggregate to any caller that only reads ``degraded``. Every
            # entry now carries ``measured: False`` alongside the existing
            # ``advisory: "probe_error"`` reason, matching the shape
            # ``step_pipeline_health``'s own probes use for an unmeasured
            # result (PRD-CORE-263-FR03).
            unmeasured_signal = {"degraded": False, "measured": False, "advisory": "probe_error"}
            return {
                "degraded": False,
                "measured": False,
                "advisory": "health_probe_failed",
                "error": str(exc),
                "sync_push": dict(unmeasured_signal),
                "graph_edges": dict(unmeasured_signal),
                "embedding_coverage": dict(unmeasured_signal),
                "recall_feedback": dict(unmeasured_signal),
                "bandit_state": dict(unmeasured_signal),
            }
