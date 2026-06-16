"""Knowledge topology tools — auto-generated topic documents from tag clusters.

The ``trw_knowledge_sync`` MCP tool wrapper was removed by PRD-FIX-076 (dead
surface — zero skill/agent/hook callers). The underlying knowledge-sync logic
lives in ``trw_mcp.state.knowledge_topology.execute_knowledge_sync`` and the
graph backfill in ``trw_mcp.state.memory_adapter.backfill_graph``; both remain
load-bearing (consumed during deliver/backfill via ``_ceremony_deliver_steps``
and surfaced as a session-start advisory).

``register_knowledge_tools`` is retained as a no-op registrar so the server
wiring in ``server/_tools.py`` and the conftest tool-group registry keep a
stable call site.
"""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_knowledge_tools(server: FastMCP) -> None:
    """No-op registrar — knowledge-sync MCP tool removed by PRD-FIX-076.

    The knowledge-topology sync logic remains available as an internal function
    in ``trw_mcp.state.knowledge_topology``; only the agent-facing
    ``@server.tool`` surface was removed. Kept as a no-op so existing
    registration call sites do not need to change.
    """
