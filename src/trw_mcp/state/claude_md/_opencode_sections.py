"""OpenCode instruction renderer compatibility surface.

PRD-CORE-115 introduced per-client instruction generation. v25 keeps the
``model_family`` argument for compatibility, but rendering delegates to the
portable ProtocolRenderer path instead of family-specific prompt text.
"""

from __future__ import annotations

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._renderer import ProtocolRenderer


def render_opencode_instructions(model_family: str) -> str:
    """Render portable instructions content for OpenCode.

    Args:
        model_family: Legacy family hint; accepted but not required.

    Returns:
        Portable Markdown instructions for OpenCode.
    """
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family=model_family or "generic",
    )
    return renderer.render_opencode_instructions()
