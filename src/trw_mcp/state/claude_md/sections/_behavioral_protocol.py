"""Behavioral-protocol section renderers.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.
"""

from __future__ import annotations

# PRD-CORE-149-FR01: resolve patchable dependencies via the facade.
import trw_mcp.state.claude_md._static_sections as _facade
from trw_mcp.state.claude_md._renderer import ProtocolRenderer
from trw_mcp.state.claude_md.sections._memory_routing import _format_learning_session_claim


def render_imperative_opener() -> str:
    """Render the value-oriented opener for the TRW auto-generated section.

    This is the highest-signal text in CLAUDE.md — it loads on every message.
    Designed to make ``trw_session_start()`` the obvious first action by
    framing it as the gateway to accumulated team knowledge. The session-start
    hook then delivers the full operational briefing (phases, delegation,
    watchlist) so CLAUDE.md stays compact.

    Prompt engineering: Uses concrete benefit framing (what you gain) rather
    than threat framing (what you lose). Specific numbers ground the claims.
    The "call this first" pattern leverages primacy bias in instruction
    following — the first concrete action in instructions gets highest
    compliance.

    Returns:
        Markdown string with role framing and session_start trigger.
    """
    analytics_claim = _format_learning_session_claim()
    return (
        "Your primary role is **orchestration** — delegate to focused agents "
        "when a task benefits from its own context window. Focused subagents get "
        "deeper context per task than the parent session can hold; subagent "
        "results return with tighter scope and less distraction. Reserve "
        "self-implementation for trivial edits (≤3 lines, 1 file).\n"
        "\n"
        "**Your first action in every session must be `trw_session_start()`.**\n"
        "\n"
        f"This single call loads everything you need: {analytics_claim}, "
        "any active run state you can resume, "
        "and the full operational protocol (delegation guidance, phase gates, "
        "quality rubrics). Without it, you start from zero — with it, you "
        "start from the team’s accumulated experience.\n"
        "\n"
        "After `trw_session_start()`, save progress with `trw_checkpoint()` "
        "after milestones, and close with `trw_deliver()` so your discoveries "
        "persist for future agents.\n"
        "\n"
    )


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml."""
    from trw_mcp.exceptions import StateError

    config = _facade.get_config()
    reader = _facade.FileStateReader()

    proto_path = _facade.resolve_project_root() / config.trw_dir / config.context_dir / "behavioral_protocol.yaml"
    if not proto_path.exists():
        return ""
    try:
        data = reader.read_yaml(proto_path)
    except (StateError, ValueError, TypeError):
        return ""
    directives = data.get("directives", [])
    if not directives or not isinstance(directives, list):
        return ""
    lines = [f"- {d}" for d in directives[:12]]
    lines.append("")
    return "\n".join(lines) + "\n"


def generate_behavioral_protocol_md() -> str:
    """Generate the full behavioral protocol as a static markdown file."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile, ceremony_mode="FULL")
    return renderer.render_behavioral_protocol()


def render_minimal_protocol() -> str:
    """Render a shortened ceremony protocol for local model AGENTS.md."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile, ceremony_mode="MINIMAL")
    return renderer.render_minimal_protocol()
