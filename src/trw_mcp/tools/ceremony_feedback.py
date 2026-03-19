"""Ceremony feedback MCP tools — PRD-CORE-069-FR06/FR08.

Tools for querying ceremony status, approving proposals, and reverting changes.
"""

from __future__ import annotations

from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.models.typed_dicts import (
    CeremonyApproveResult,
    CeremonyRevertResult,
    CeremonyStatusResult,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def register_ceremony_feedback_tools(server: FastMCP) -> None:
    """Register ceremony feedback tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_ceremony_status(
        task_class: str | None = None,
    ) -> CeremonyStatusResult:
        """Check ceremony feedback status — proposals, escalations, and quality trends.

        Shows per-task-class ceremony quality metrics. When conditions are met,
        includes reduction proposals that require human approval.

        Args:
            task_class: Optional task class to query (documentation, feature,
                refactor, security, infrastructure). If None, returns all.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import get_ceremony_status

        trw_dir = resolve_trw_dir()
        return cast("CeremonyStatusResult", get_ceremony_status(trw_dir, task_class))

    @server.tool()
    @log_tool_call
    def trw_ceremony_approve(
        proposal_id: str,
    ) -> CeremonyApproveResult:
        """Approve a pending ceremony reduction proposal.

        Ceremony reductions require explicit human approval. This tool
        applies an approved proposal, changing the ceremony tier for the
        specified task class.

        Args:
            proposal_id: The proposal ID from trw_ceremony_status output.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import approve_proposal

        trw_dir = resolve_trw_dir()
        return cast("CeremonyApproveResult", approve_proposal(trw_dir, proposal_id))

    @server.tool()
    @log_tool_call
    def trw_ceremony_revert(
        change_id: str,
    ) -> CeremonyRevertResult:
        """Revert a ceremony tier change by change_id.

        Restores the prior ceremony tier for a task class. Takes effect
        immediately within the current session.

        Args:
            change_id: The change ID from trw_ceremony_approve output.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import revert_change

        trw_dir = resolve_trw_dir()
        return cast("CeremonyRevertResult", revert_change(trw_dir, change_id))
