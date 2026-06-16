"""Ceremony feedback MCP tools — PRD-CORE-069-FR06/FR08.

The ``trw_ceremony_status`` / ``trw_ceremony_approve`` / ``trw_ceremony_revert``
MCP tool wrappers were removed by PRD-FIX-076 (dead surface — zero
skill/agent/hook callers). The underlying ceremony-feedback state logic lives in
``trw_mcp.state.ceremony_feedback`` (``get_ceremony_status`` / ``approve_proposal``
/ ``revert_change``) and remains load-bearing: it is consumed by
``_deferred_steps_telemetry.py``, ``models/typed_dicts/_ceremony.py``, and the
ceremony escalation/sanitize modules independently of the MCP surface.

``register_ceremony_feedback_tools`` is retained as a no-op registrar so the
server wiring in ``server/_tools.py`` and the conftest tool-group registry keep
a stable call site.
"""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_ceremony_feedback_tools(server: FastMCP) -> None:
    """No-op registrar — ceremony-feedback MCP tools removed by PRD-FIX-076.

    The ceremony-feedback state APIs remain available as internal functions in
    ``trw_mcp.state.ceremony_feedback``; only the agent-facing ``@server.tool``
    surface was removed. Kept as a no-op so existing registration call sites do
    not need to change.
    """
