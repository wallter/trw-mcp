"""AG-04: Tool-return telemetry enrichment for Antigravity CLI sessions.

# Managed by TRW — no trw_distill imports permitted.

Pull-only enrichment: records a channel event when trw_before_edit_hint,
trw_codebase_risk_report, or trw_entity_risk_map are called from an
Antigravity CLI session.

Default tier: T1 for antigravity-cli.
Fail-open: all exceptions caught, never propagated.

PRD-DIST-2404 FR19.
"""

from __future__ import annotations

import structlog

from trw_mcp.channels._distill_telemetry import emit_tool_call, resolve_client_profile

log = structlog.get_logger(__name__)

__all__ = [
    "AG04_CHANNEL_ID",
    "get_default_tier_for_antigravity",
    "should_emit_enrichment",
]

AG04_CHANNEL_ID = "ag-04-tool-return-enrichment"

_ANTIGRAVITY_CLIENT = "antigravity-cli"
_DEFAULT_TIER = "T1"


def get_default_tier_for_antigravity() -> str:
    """Return the default AG-04 tier for antigravity-cli sessions.

    Returns:
        ``"T1"`` — the AG-04 default tier (FR19).
    """
    return _DEFAULT_TIER


def should_emit_enrichment(client: str | None = None) -> bool:
    """Return True when the current session is an Antigravity CLI session.

    Uses the provided *client* or falls back to ``resolve_client_profile()``.

    Args:
        client: Explicit client profile string, or None to auto-detect.

    Returns:
        True if client matches ``"antigravity-cli"``.
    """
    resolved = client or resolve_client_profile()
    return resolved == _ANTIGRAVITY_CLIENT


def emit_ag04_tool_return(
    *,
    tool_name: str,
    client: str | None = None,
) -> None:
    """Emit an AG-04 pull event for an Antigravity CLI tool call (FR19).

    Fail-open: catches all exceptions. Never raises.

    Args:
        tool_name: MCP tool name (e.g. ``"trw_before_edit_hint"``).
        client: Explicit client profile, or None to auto-detect from env.
    """
    try:
        resolved = client or resolve_client_profile()
        emit_tool_call(
            tool_name=tool_name,
            client=resolved,
            channel_id=AG04_CHANNEL_ID,
            tier=_DEFAULT_TIER,
        )
    except Exception:
        pass
