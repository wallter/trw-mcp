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

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_ceremony_status(
        task_class: str | None = None,
    ) -> CeremonyStatusResult:
        """Report ceremony feedback state — proposals, escalations, quality trends.

        Use when:
        - You want to see per-task-class ceremony quality metrics.
        - You need to know whether any reduction proposals are awaiting approval.

        Input:
        - task_class: optional filter (documentation, feature, refactor, security,
          infrastructure). None returns every tracked class.

        Output: CeremonyStatusResult with fields
        {classes: list[{task_class, tier, pass_rate, sample_size}],
         proposals: list[{proposal_id, task_class, from_tier, to_tier, reason}],
         escalations: list[dict]}.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import get_ceremony_status

        trw_dir = resolve_trw_dir()
        return cast("CeremonyStatusResult", get_ceremony_status(trw_dir, task_class))

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_ceremony_approve(
        proposal_id: str,
    ) -> CeremonyApproveResult:
        """Approve a pending ceremony reduction proposal.

        Use when:
        - A reviewer has seen a proposal in trw_ceremony_status and wants it applied.

        Input:
        - proposal_id: the ID surfaced by trw_ceremony_status (required).

        Output: CeremonyApproveResult with fields
        {status: "approved"|"not_found"|"error", change_id?: str,
         task_class?: str, from_tier?: str, to_tier?: str}.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import approve_proposal

        trw_dir = resolve_trw_dir()
        return cast("CeremonyApproveResult", approve_proposal(trw_dir, proposal_id))

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_ceremony_revert(
        change_id: str,
    ) -> CeremonyRevertResult:
        """Revert a ceremony tier change, restoring the prior tier.

        Use when:
        - A previously approved ceremony reduction needs to be rolled back.

        Input:
        - change_id: the change ID returned from trw_ceremony_approve (required).

        Output: CeremonyRevertResult with fields
        {status: "reverted"|"not_found"|"error", task_class?: str,
         restored_tier?: str}.
        """
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import revert_change

        trw_dir = resolve_trw_dir()
        return cast("CeremonyRevertResult", revert_change(trw_dir, change_id))
