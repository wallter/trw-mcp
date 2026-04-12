"""OpenCode model-family instruction renderers — part of claude_md static sections.

PRD-CORE-115 FR04/FR05: Produces materially different instruction content for
each model family (qwen, gpt, claude, generic) covering context budget, reasoning
syntax, tool-use patterns, and known limitations.

PRD-CORE-131: Delegates to ``ProtocolRenderer`` for centralized generation.

Public surface: render_opencode_instructions(model_family)
"""

from __future__ import annotations

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._renderer import ProtocolRenderer


def render_opencode_instructions(model_family: str) -> str:
    """Render instructions content for OpenCode .opencode/INSTRUCTIONS.md.

    Args:
        model_family: One of 'qwen', 'gpt', 'claude', or 'generic'.

    Returns:
        Markdown string for OpenCode-specific instructions.
    """
    if model_family not in ("qwen", "gpt", "claude"):
        model_family = "generic"

    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family=model_family,
    )
    return renderer.render_opencode_instructions()
