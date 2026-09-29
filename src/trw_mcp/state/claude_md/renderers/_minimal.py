"""MINIMAL-mode ceremony protocol body (PRD-CORE-131-FR04).

Belongs to the ``_renderer.py`` facade: ``ProtocolRenderer.render_minimal_protocol``
delegates here. Split out at the mode seam so the facade stays under the 350-line
module ceiling (INT-RED-RENDERER-LOC, 2026-09-26).

PRD-QUAL-104-FR03: the light-ceremony body MAY omit the full tool table but MUST still
emit the session-start mandate and the deliver-gate statement (the file is the only
protocol carrier). The gate language comes from the single canonical
``render_deliver_gate_statement()`` rather than a hand-copied string, so this path can
never drift gate-less or stale (P1 audit fix, 2026-06-11). Section loaders are imported
function-locally to avoid a ``sections`` <-> ``_renderer`` import cycle.
"""

from __future__ import annotations

from trw_mcp.models.config._client_profile import ClientProfile


def render_minimal_protocol(client_profile: ClientProfile, session_boundary_text: str) -> str:
    """Render the shortened ceremony protocol a light-mode AGENTS.md carries."""
    from trw_mcp.bootstrap._client_integrations import client_transport_guidance
    from trw_mcp.models.config._pre_edit_channels import render_pre_edit_hint_instruction
    from trw_mcp.state.claude_md.sections._delegation import (
        MEMORY_ROUTING_POINTER,
        OFFLINE_POINTER,
        SURFACE_POINTER,
    )
    from trw_mcp.state.claude_md.sections._feedback import render_feedback_reporting
    from trw_mcp.state.claude_md.sections._tool_lifecycle import DELEGATION_RULE, render_deliver_gate_statement

    # PRD-CORE-215-FR06: the light-ceremony AGENTS.md is the only protocol
    # carrier, so it still ships the transport-loss retry protocol.
    # PRD-CORE-301-FR13: memory routing, the offline table and the capability
    # listing are the same on-demand pointers the shared block carries.
    return (
        "TRW tools persist your work across sessions:\n"
        "- **Start**: call `trw_session_start()` to load prior learnings\n"
        "- **Accept completed work**: `trw_deliver()` under the delivery gates\n"
        "- **Verify**: Run project-native checks after meaningful changes \u2014 fix failures before moving on.\n"
        + render_pre_edit_hint_instruction()
        + "\n"
        + DELEGATION_RULE
        + "\n"
        + MEMORY_ROUTING_POINTER
        + "\n"
        + render_feedback_reporting(client_profile)
        + "\n"
        + render_deliver_gate_statement()
        + "\n"
        + session_boundary_text
        + "\n"
        + OFFLINE_POINTER
        + "\n"
        + client_transport_guidance(client_profile.client_id or "agents")
        + "\n\n"
        + SURFACE_POINTER
    )
