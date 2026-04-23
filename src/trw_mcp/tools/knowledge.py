"""Knowledge topology tools — auto-generated topic documents from tag clusters.

Provides trw_knowledge_sync tool that clusters learnings by tag co-occurrence
and generates topic documents in .trw/knowledge/.
"""

from __future__ import annotations

import time
from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import KnowledgeSyncResultDict
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.knowledge_topology import execute_knowledge_sync
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def register_knowledge_tools(server: FastMCP) -> None:
    """Register knowledge topology tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_knowledge_sync(
        dry_run: bool = False,
    ) -> KnowledgeSyncResultDict:
        """Cluster learnings by tag co-occurrence and emit per-topic Markdown docs.

        Use when:
        - The learning store has grown enough that browsing by topic beats keyword recall.
        - You want to produce shareable Markdown summaries for the team.
        - You want to dry-run threshold checks without writing files.

        Clusters learnings by Jaccard similarity on tag sets and writes one
        document per cluster to ``.trw/knowledge/``.

        Input:
        - dry_run: report threshold status without writing files.

        Output: KnowledgeSyncResultDict with fields
        {topics_generated: int, entries_clustered: int, threshold_met: bool,
         elapsed_seconds: float, status: str}.
        """
        config = get_config()
        trw_dir = resolve_trw_dir()
        start = time.monotonic()

        result = execute_knowledge_sync(trw_dir, config, dry_run=dry_run)

        elapsed = round(time.monotonic() - start, 2)
        result["elapsed_seconds"] = elapsed

        logger.info(
            "knowledge_sync_complete",
            topics_generated=result.get("topics_generated", 0),
            entries_clustered=result.get("entries_clustered", 0),
            elapsed_seconds=elapsed,
            threshold_met=result.get("threshold_met", False),
        )

        return cast("KnowledgeSyncResultDict", result)
