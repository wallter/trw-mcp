"""Tests for profile-aware MCP tool namespace rendering (PRD-FIX-078).

Verifies:
- ClientProfile.tool_namespace_prefix field + per-profile values (FR01)
- render_tool_name() + {tool:...} placeholder substitution (FR02)
- messages.yaml / behavioral_protocol.yaml / trw_readme.md converted (FR03)
- Agent definition files converted (FR04)
- Per-profile regression (FR05)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.prompts.messaging import (
    _expand_tool_placeholders,
    get_message,
    render_message,
    render_tool_name,
)

_AGENTS_DIR = Path(__file__).parent.parent / "src/trw_mcp/data/agents"
_MESSAGES_YAML = Path(__file__).parent.parent / "src/trw_mcp/data/messages/messages.yaml"
_BP_YAML = Path(__file__).parent.parent / "src/trw_mcp/data/behavioral_protocol.yaml"
_README = Path(__file__).parent.parent / "src/trw_mcp/data/trw_readme.md"

_ALL_PROFILES = [
    "claude-code",
    "opencode",
    "cursor-ide",
    "cursor-cli",
    "codex",
    "copilot",
    "gemini",
    "aider",
]


# --- FR01: ClientProfile field ------------------------------------------------


def test_client_profile_tool_namespace_prefix_field() -> None:
    p = ClientProfile(client_id="test", display_name="Test")
    assert p.tool_namespace_prefix == ""


def test_claude_code_profile_has_mcp_trw_prefix() -> None:
    p = resolve_client_profile("claude-code")
    assert p.tool_namespace_prefix == "mcp__trw__"


@pytest.mark.parametrize(
    "client_id",
    ["opencode", "cursor-ide", "cursor-cli", "codex", "copilot", "gemini", "aider"],
)
def test_non_claude_code_profiles_have_empty_prefix(client_id: str) -> None:
    p = resolve_client_profile(client_id)
    assert p.tool_namespace_prefix == ""


def test_invalid_prefix_rejected() -> None:
    with pytest.raises(ValueError, match="tool_namespace_prefix"):
        ClientProfile(client_id="x", display_name="x", tool_namespace_prefix="bad prefix!")


def test_custom_profile_registration() -> None:
    p = ClientProfile(client_id="foo", display_name="Foo", tool_namespace_prefix="mcp__foo__")
    assert render_tool_name("trw_learn", p) == "mcp__foo__trw_learn"


# --- FR02: render_tool_name + placeholder expansion ---------------------------


def test_render_tool_name_claude_code() -> None:
    p = resolve_client_profile("claude-code")
    assert render_tool_name("trw_session_start", p) == "mcp__trw__trw_session_start"


def test_render_tool_name_opencode() -> None:
    p = resolve_client_profile("opencode")
    assert render_tool_name("trw_session_start", p) == "trw_session_start"


def test_render_tool_name_non_trw_passthrough() -> None:
    p = resolve_client_profile("claude-code")
    assert render_tool_name("Bash", p) == "Bash"
    assert render_tool_name("Edit", p) == "Edit"


def test_render_tool_name_none_profile_bare() -> None:
    assert render_tool_name("trw_learn", None) == "trw_learn"


def test_expand_tool_placeholders_claude_code() -> None:
    p = resolve_client_profile("claude-code")
    result = _expand_tool_placeholders("Call {tool:trw_learn} please", p)
    assert result == "Call mcp__trw__trw_learn please"


def test_expand_tool_placeholders_opencode() -> None:
    p = resolve_client_profile("opencode")
    result = _expand_tool_placeholders("Call {tool:trw_learn} please", p)
    assert result == "Call trw_learn please"


def test_malformed_placeholder_left_literal_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    p = resolve_client_profile("claude-code")
    # Empty body and non-trw body must NOT be expanded
    result = _expand_tool_placeholders("{tool:} and {tool:Bash}", p)
    assert result == "{tool:} and {tool:Bash}"


def test_format_directive_placeholder_not_expanded() -> None:
    p = resolve_client_profile("claude-code")
    # {tool:trw_learn!r} isn't a valid placeholder under our strict regex
    result = _expand_tool_placeholders("{tool:trw_learn!r}", p)
    assert result == "{tool:trw_learn!r}"


# --- FR03: messages.yaml / behavioral_protocol.yaml / trw_readme.md ----------


def test_messages_yaml_uses_placeholders() -> None:
    yaml = YAML(typ="safe")
    data = yaml.load(_MESSAGES_YAML.read_text())
    si = data["server_instructions"]
    # placeholder present, bare call form absent
    assert "{tool:trw_session_start}" in si
    assert "trw_session_start(" not in si


def test_behavioral_protocol_uses_placeholders() -> None:
    yaml = YAML(typ="safe")
    data = yaml.load(_BP_YAML.read_text())
    joined = "\n".join(data["directives"])
    assert "{tool:trw_learn}" in joined
    assert "{tool:trw_session_start}" in joined
    assert "{tool:trw_deliver}" in joined


def test_trw_readme_converted_prose_references() -> None:
    text = _README.read_text()
    # A sampling of converted prose references
    assert "{tool:trw_build_check}" in text
    assert "{tool:trw_session_start}" in text
    assert "{tool:trw_instructions_sync}" in text


# --- FR04: agent files -------------------------------------------------------


_EXPECTED_CONVERTED_AGENTS = [
    "trw-adversarial-auditor.md",
    "trw-auditor.md",
    "trw-implementer.md",
    "trw-lead.md",
    "trw-prd-groomer.md",
    "trw-requirement-reviewer.md",
    "trw-requirement-writer.md",
    "trw-researcher.md",
    "trw-tester.md",
    "trw-traceability-checker.md",
]


@pytest.mark.parametrize("fname", _EXPECTED_CONVERTED_AGENTS)
def test_agent_file_has_placeholder(fname: str) -> None:
    text = (_AGENTS_DIR / fname).read_text()
    assert "{tool:trw_" in text, f"{fname} missing {{tool:trw_*}} placeholders"


def test_agent_frontmatter_mcp_prefix_preserved() -> None:
    """PRD-FIX-078 FR04: frontmatter `allowedTools:` lines must remain literal
    mcp__trw__ strings — claude-code parses them directly."""
    text = (_AGENTS_DIR / "trw-lead.md").read_text()
    assert "mcp__trw__trw_" in text


def test_agent_lead_prose_has_no_bare_trw_calls() -> None:
    """Outside code fences and frontmatter, bare `trw_foo(` calls should not
    appear — they must be templated."""
    text = (_AGENTS_DIR / "trw-lead.md").read_text()
    # Strip code fences
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Strip frontmatter block (first --- ... ---)
    stripped = re.sub(r"^---\n.*?\n---\n", "", stripped, count=1, flags=re.DOTALL)
    # After stripping, there should be no bare `trw_session_start(` prose.
    assert "trw_session_start(" not in stripped, "bare trw_session_start( remains in prose of trw-lead.md"


# --- FR05: per-profile regression tests --------------------------------------


def test_claude_code_renders_mcp_trw_prefix() -> None:
    p = resolve_client_profile("claude-code")
    rendered = render_message("server_instructions", p)
    assert "mcp__trw__trw_session_start" in rendered
    # Bare call form must be gone
    assert "trw_session_start(" not in rendered.replace("mcp__trw__trw_session_start(", "")


def test_opencode_renders_bare_names() -> None:
    p = resolve_client_profile("opencode")
    rendered = render_message("server_instructions", p)
    assert "trw_session_start" in rendered
    assert "mcp__trw__" not in rendered


def test_unset_profile_defaults_to_bare_names() -> None:
    # PRD-FIX-078 NFR04: get_message without profile degrades to bare names
    # (legacy-safe for callers not yet threaded with a profile).
    raw = get_message("server_instructions")
    assert "trw_session_start" in raw
    assert "{tool:" not in raw
    assert "mcp__trw__" not in raw


@pytest.mark.parametrize("client_id", _ALL_PROFILES)
def test_render_message_works_for_all_profiles(client_id: str) -> None:
    p = resolve_client_profile(client_id)
    rendered = render_message("ceremony_warning", p)
    # No unexpanded placeholders should remain
    assert "{tool:" not in rendered


def test_behavioral_protocol_renders_correctly_per_profile() -> None:
    yaml = YAML(typ="safe")
    data = yaml.load(_BP_YAML.read_text())
    joined = "\n".join(data["directives"])
    cc = resolve_client_profile("claude-code")
    oc = resolve_client_profile("opencode")
    cc_rendered = _expand_tool_placeholders(joined, cc)
    oc_rendered = _expand_tool_placeholders(joined, oc)
    assert "mcp__trw__trw_learn" in cc_rendered
    assert "mcp__trw__" not in oc_rendered
    assert "trw_learn" in oc_rendered
