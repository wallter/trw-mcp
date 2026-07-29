"""PRD-CORE-149 FR08: generated OpenCode AGENTS.md has correct identity."""

from __future__ import annotations

import pytest

from tests.test_claude_md_sync import _run_sync
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


def test_public_sync_writes_opencode_agents_md_without_claude_literal(tmp_path) -> None:
    """Drive the production sync entrypoint and inspect its configured file."""
    (tmp_path / ".opencode").mkdir()

    result = _run_sync(tmp_path, client="opencode")

    # PRD-CORE-240-FR04: the leak assertion is unchanged in intent; what changed is
    # WHICH file opencode reads. It owns .opencode/INSTRUCTIONS.md now, and the shared
    # AGENTS.md is left entirely alone.
    agents_md = tmp_path / "AGENTS.md"
    assert result["agents_md_synced"] is False
    assert not agents_md.exists()

    owned = tmp_path / ".opencode" / "INSTRUCTIONS.md"
    assert owned.is_file()
    content = owned.read_text(encoding="utf-8")
    assert "Claude Code" not in content
    hook_env = (tmp_path / ".trw" / "runtime" / "hook-env.sh").read_text(encoding="utf-8")
    assert "HOOKS_ENABLED=false" in hook_env
    assert "NUDGE_ENABLED=false" in hook_env


def test_auto_detected_opencode_sync_writes_light_hook_policy(tmp_path) -> None:
    """FR04 default path: auto detection and hook policy resolve one client."""
    (tmp_path / ".opencode").mkdir()

    _run_sync(tmp_path, client="auto")

    hook_env = (tmp_path / ".trw" / "runtime" / "hook-env.sh").read_text(encoding="utf-8")
    assert "HOOKS_ENABLED=false" in hook_env
    assert "NUDGE_ENABLED=false" in hook_env
    assert "TRW_CLIENT_DISPLAY_NAME=OpenCode" in hook_env


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
