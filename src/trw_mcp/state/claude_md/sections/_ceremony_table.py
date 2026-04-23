"""Ceremony-table section renderers — all delegate to ProtocolRenderer.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.
"""

from __future__ import annotations

# PRD-CORE-149-FR01: resolve ``get_config`` via the facade so legacy
# ``monkeypatch.setattr(_static_sections, "get_config", ...)`` patches
# continue to work after decomposition.
import trw_mcp.state.claude_md._static_sections as _facade
from trw_mcp.state.claude_md._renderer import ProtocolRenderer


def render_ceremony_quick_ref() -> str:
    """Render compact ceremony quick-reference card for CLAUDE.md."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile)
    return renderer.render_ceremony_quick_ref()


def render_phase_descriptions() -> str:
    """Render phase arrow diagram and description list."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile)
    return renderer.render_phase_descriptions()


def render_ceremony_table() -> str:
    """Render ceremony tools as a markdown table."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile)
    return renderer.render_ceremony_table()


def render_ceremony_flows() -> str:
    """Render quick task and full run example flows."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile)
    return renderer.render_ceremony_flows()
