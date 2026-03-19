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

    @server.tool()
    @log_tool_call
    def trw_knowledge_sync(
        dry_run: bool = False,
    ) -> KnowledgeSyncResultDict:
        """Auto-generate topic documents from tag clusters in the learning store.

        Clusters learnings by Jaccard similarity on tag co-occurrence and
        writes one Markdown document per cluster to .trw/knowledge/. Use
        dry_run=True to check entry count vs threshold without side effects.

        Args:
            dry_run: When True, report threshold status without writing files.
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
