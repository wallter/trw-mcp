"""Behavioral-protocol section renderers.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.
"""

from __future__ import annotations

# PRD-CORE-149-FR01: resolve patchable dependencies (``get_config``,
# ``FileStateReader``) via the facade so legacy ``monkeypatch.setattr(
# _static_sections, name, ...)`` patches keep working.
#
# Exception: the project root is NOT routed through ``_facade``. It is
# late-resolved via ``_paths.resolve_project_root()`` at call time (see the
# import note below) so tests that patch ``trw_mcp.state._paths.resolve_project_root``
# redirect the write/read target — the facade alias is only for config /
# FileStateReader / time / yaml.
import trw_mcp.state.claude_md._static_sections as _facade

# Resolve the project root via LATE lookup through ``_paths`` (read at call
# time, not bound at import) so the renderer honours runtime monkeypatching of
# ``trw_mcp.state._paths.resolve_project_root`` and never targets the real repo.
from trw_mcp.state import _paths
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
        "**Your first action in every session must be `trw_session_start()`.**\n"
        "\n"
        f"This single call loads everything you need: {analytics_claim}, "
        "any active run state you can resume, "
        "and the full operational protocol (phase gates, quality rubrics). "
        "Without it, you start from zero — with it, you "
        "start from the team’s accumulated experience.\n"
        "\n"
        "After `trw_session_start()`, save progress with `trw_checkpoint()` "
        "after milestones, and close with `trw_deliver()` so your discoveries "
        "persist for future agents.\n"
        "\n"
        "**Delegation**: use focused helpers when the harness supports it and "
        "file ownership is clear. When it does not, run the same shards "
        "sequentially. Delegation is an optimization, not a dependency.\n"
        "\n"
    )


def render_behavioral_protocol() -> str:
    """Render behavioral directives from .trw/context/behavioral_protocol.yaml."""
    from trw_mcp.exceptions import StateError

    config = _facade.get_config()
    reader = _facade.FileStateReader()

    proto_path = _paths.resolve_project_root() / config.trw_dir / config.context_dir / "behavioral_protocol.yaml"
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
