"""Tests for the unified ProtocolRenderer."""

from __future__ import annotations

from typing import cast

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._renderer import ProtocolRenderer
from trw_mcp.state.claude_md._templates import CEREMONY_TOOLS, CeremonyTool


@pytest.fixture
def mock_config(monkeypatch) -> None:
    """Mock get_config to return a default TRWConfig."""
    monkeypatch.setattr("trw_mcp.state.claude_md._renderer.get_config", lambda: TRWConfig())
    monkeypatch.setattr("trw_mcp.state.claude_md._static_sections.get_config", lambda: TRWConfig())


def test_renderer_initialization(mock_config: None):
    """Verify ProtocolRenderer can be initialized."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test-client", display_name="test-client"))
    assert renderer.client_profile.client_id == "test-client"


def test_render_ceremony_table(mock_config: None):
    """Verify that the ceremony table is rendered from CEREMONY_TOOLS."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test-client", display_name="test-client"))
    table = renderer.render_ceremony_table()

    # Check for header
    assert "| Phase | Tool | When to Use | What It Does | Example |" in table
    # Check for a sample tool
    trw_learn_tool = next(tool for tool in CEREMONY_TOOLS if tool.tool == "trw_learn")
    assert f"`{trw_learn_tool.tool}`" in table
    assert trw_learn_tool.when in table
    assert trw_learn_tool.what in table


def test_render_ceremony_quick_ref_gemini_note(mock_config: None):
    """Verify Gemini-specific note is injected into the quick reference table."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="gemini", display_name="gemini"))
    table = renderer.render_ceremony_quick_ref()
    assert "**CRITICAL: Only record actual insights, patterns, or gotchas.**" in table


def test_model_specific_reasoning_injection(mock_config: None):
    """Verify Qwen-specific /think tags are injected for opencode."""
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family="qwen",
    )
    instructions = renderer.render_opencode_instructions()
    assert "/think" in instructions
    assert "Qwen-Coder-Next" in instructions


def test_ceremony_mode_switching(mock_config: None):
    """Verify renderer output changes with ceremony mode."""
    full_renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="claude-code", display_name="claude-code"), ceremony_mode="FULL")
    minimal_renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="claude-code", display_name="claude-code"), ceremony_mode="MINIMAL")

    full_output = full_renderer.render_behavioral_protocol()
    minimal_output = minimal_renderer.render_minimal_protocol()

    assert "Execution Phases" in full_output
    assert "Execution Phases" not in minimal_output
    assert "Run tests after each change" in minimal_output


def test_gemini_instructions_parity(mock_config: None):
    """Verify generated Gemini instructions match the old hardcoded format."""
    from trw_mcp.bootstrap._gemini import _gemini_instructions_content

    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="gemini", display_name="gemini"))
    new_content = renderer.render_gemini_instructions()

    # The original function includes the start/end markers, so we add them for comparison
    original_content = _gemini_instructions_content()

    assert new_content.strip() == original_content.strip()

