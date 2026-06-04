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
        """Probe the five compounding-pipeline signals (sync_push, graph_edges,
        embedding_coverage, recall_feedback, bandit_state). Returns a structured
        report with degraded flag and advisory.

        Use when:
        - ``trw_session_start`` returns a ``pipeline_health_advisory`` and you need
          the full per-signal breakdown to diagnose which subsystem is degraded.
        - Performing a routine operator health check outside of ceremony.

        Checks: sync_push (consecutive_failures + last_push_at age),
        graph_edges (knowledge graph empty?), embedding_coverage (< 10%?),
        recall_feedback (all recall_count=0?), and bandit_state (mtime stale?).

        Returns a structured report with:
        - ``degraded``: True if any signal is degraded.
        - ``advisory``: Compact single-line string naming degraded signals.
        - Per-signal sub-dicts with detailed status.

        All probes are read-only and fail-open individually.
        """
        try:
            from trw_mcp.state._paths import resolve_trw_dir

            trw_dir = resolve_trw_dir()
            return step_pipeline_health(trw_dir)
        except Exception as exc:  # justified: fail-open, tool must never crash
            logger.warning("pipeline_health_tool_failed", error=str(exc))
            return {
                "degraded": False,
                "advisory": "health_probe_failed",
                "error": str(exc),
                "sync_push": {"degraded": False, "advisory": "probe_error"},
                "graph_edges": {"degraded": False, "advisory": "probe_error"},
                "embedding_coverage": {"degraded": False, "advisory": "probe_error"},
                "recall_feedback": {"degraded": False, "advisory": "probe_error"},
                "bandit_state": {"degraded": False, "advisory": "probe_error"},
            }
