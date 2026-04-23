"""PRD-CORE-149 FR08: opencode AGENTS.md contains zero 'Claude Code' literals.

Renders the opencode protocol surface via ``ProtocolRenderer`` against the
opencode profile and asserts the output is free of claude-code-specific
identifiers. Uses the renderer directly rather than driving the full sync
(keeps the test deterministic and independent of tmp-fs / IDE detection).
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state.claude_md._renderer import ProtocolRenderer

pytestmark = pytest.mark.unit


def _render_opencode_surface() -> str:
    profile = resolve_client_profile("opencode")
    # opencode is a light-mode profile -> MINIMAL is the right ceremony mode.
    r = ProtocolRenderer(client_profile=profile, ceremony_mode="MINIMAL")
    # Render the full stack of protocol sections we expose.
    parts = [
        r.render_behavioral_protocol(),
        r.render_ceremony_quick_ref(),
        r.render_ceremony_table(),
        r.render_minimal_protocol(),
        r.render_closing_reminder(),
    ]
    return "\n\n".join(parts)


def test_opencode_surface_has_zero_claude_code_literals() -> None:
    output = _render_opencode_surface()
    assert "Claude Code" not in output, (
        "opencode protocol surface must not hardcode 'Claude Code' -- "
        "use profile.display_name / template substitution instead."
    )


def test_opencode_profile_has_correct_identity_fields() -> None:
    """FR08 surrogate: opencode profile is wired with the right display_name,
    so any future profile-aware render surface will emit 'OpenCode' where
    the template says {client_display_name}."""
    profile = resolve_client_profile("opencode")
    assert profile.display_name == "OpenCode"
    assert profile.client_id == "opencode"
    assert profile.config_dir == ".opencode"


def test_claude_code_profile_identity_is_preserved() -> None:
    """Regression: templating changes must not alter the claude-code profile identity."""
    profile = resolve_client_profile("claude-code")
    assert profile.display_name == "Claude Code"
    assert profile.client_id == "claude-code"
    assert profile.config_dir == ".claude"
