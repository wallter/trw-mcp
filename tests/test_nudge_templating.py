"""PRD-CORE-149 FR02/FR03/FR12: profile-aware nudge templating.

Validates that ``format_nudge`` correctly substitutes client-display-name and
client-config-dir placeholders, preserves literals for the claude-code
profile, and falls back with a ``profile.fallback`` structlog warn when the
profile is missing required fields.

These tests also enforce the no-hardcoded-"Claude Code" invariant across the
live nudge-messages module -- the decomposition follow-up must keep this
green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from trw_mcp.models.config._client_profile import ClientProfile, WriteTargets
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state._nudge_messages import format_nudge

pytestmark = pytest.mark.unit

NUDGE_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "state" / "_nudge_messages.py"


def test_opencode_profile_substitutes_display_name() -> None:
    profile = resolve_client_profile("opencode")
    rendered = format_nudge("run under {client_display_name}", profile)
    assert "OpenCode" in rendered
    assert "{client_display_name}" not in rendered


def test_claude_code_profile_renders_literal_claude_code() -> None:
    profile = resolve_client_profile("claude-code")
    rendered = format_nudge("talk to {client_display_name}", profile)
    assert rendered == "talk to Claude Code"


def test_config_dir_substitutes_to_profile_config_dir() -> None:
    profile = resolve_client_profile("opencode")
    rendered = format_nudge("config lives at {client_config_dir}/", profile)
    assert rendered == "config lives at .opencode/"


def test_no_placeholder_returns_template_unchanged() -> None:
    profile = resolve_client_profile("claude-code")
    original = "no placeholders here, just plain text."
    assert format_nudge(original, profile) is original or format_nudge(original, profile) == original


def test_no_hardcoded_claude_code_in_nudge_messages_module() -> None:
    """FR02 exit criteria: zero literal 'Claude Code' in _nudge_messages.py."""
    content = NUDGE_MODULE_PATH.read_text(encoding="utf-8")
    assert "Claude Code" not in content, (
        "Literal 'Claude Code' found in _nudge_messages.py; use "
        "{client_display_name} template instead."
    )


def test_no_hardcoded_claude_config_path_in_nudge_messages_module() -> None:
    content = NUDGE_MODULE_PATH.read_text(encoding="utf-8")
    assert ".claude/" not in content, (
        "Literal '.claude/' path found in _nudge_messages.py; use "
        "{client_config_dir} template instead."
    )


def test_missing_display_name_falls_back_to_client_id() -> None:
    profile = ClientProfile(
        client_id="exotic-client",
        display_name="",
        write_targets=WriteTargets(agents_md=True, instruction_path=".exotic/I.md"),
    )
    with capture_logs() as logs:
        rendered = format_nudge("hi {client_display_name}", profile)
    assert rendered == "hi exotic-client"
    assert any(
        entry.get("event") == "profile.fallback"
        and "display_name" in str(entry.get("missing_field", ""))
        and entry.get("client_id") == "exotic-client"
        for entry in logs
    )


def test_empty_instruction_path_yields_dot_trw_config_dir() -> None:
    """FR12: ClientProfile.config_dir falls back to .trw when no instruction_path set,
    so templates render the safe default without raising."""
    profile = ClientProfile(
        client_id="exotic-client",
        display_name="Exotic",
        write_targets=WriteTargets(instruction_path=""),
    )
    rendered = format_nudge("dir={client_config_dir}", profile)
    assert rendered == "dir=.trw"


def test_none_profile_falls_back_with_warn() -> None:
    with capture_logs() as logs:
        rendered = format_nudge("{client_display_name} at {client_config_dir}", None)
    assert rendered == "<unknown> at .trw"
    assert any(entry.get("event") == "profile.fallback" for entry in logs)


def test_all_builtin_profiles_have_non_empty_display_name() -> None:
    """FR06: every registered profile must expose a human-readable display name."""
    for client_id in (
        "claude-code",
        "opencode",
        "cursor-ide",
        "cursor-cli",
        "codex",
        "copilot",
        "gemini",
        "aider",
    ):
        profile = resolve_client_profile(client_id)
        assert profile.display_name, f"profile '{client_id}' has empty display_name"
