"""Tests for the unified ProtocolRenderer (PRD-CORE-131)."""

from __future__ import annotations

import pytest

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._renderer import (
    SESSION_BOUNDARY_TEXT,
    ProtocolRenderer,
)
from trw_mcp.state.claude_md._templates import CEREMONY_TOOLS, PHASE_DESCRIPTIONS


def test_renderer_initialization() -> None:
    """Verify ProtocolRenderer can be initialized with a ClientProfile."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test-client", display_name="test-client"))
    assert renderer.client_profile.client_id == "test-client"
    assert renderer.platform == "test-client"
    assert renderer.ceremony_mode == "FULL"


def test_render_ceremony_table() -> None:
    """FR02: Verify ceremony table is rendered from CEREMONY_TOOLS data."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test-client", display_name="test-client"))
    table = renderer.render_ceremony_table()

    # Check for header
    assert "| Phase | Tool | When to Use | What It Does | Example |" in table
    # Check for a sample tool from CEREMONY_TOOLS
    trw_learn_tool = next(tool for tool in CEREMONY_TOOLS if tool.tool == "trw_learn")
    assert f"`{trw_learn_tool.tool}`" in table
    assert trw_learn_tool.when in table
    assert trw_learn_tool.what in table


def test_render_ceremony_quick_ref_generated_from_ceremony_tools() -> None:
    """FR02: Quick ref is generated from CEREMONY_TOOLS, not hardcoded."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="gemini", display_name="gemini"))
    table = renderer.render_ceremony_quick_ref()
    # All 4 quick-ref tools should appear (from CEREMONY_TOOLS data)
    for tool_name in ("trw_session_start", "trw_learn", "trw_checkpoint", "trw_deliver"):
        ct = next(t for t in CEREMONY_TOOLS if t.tool == tool_name)
        assert ct.what in table, f"{tool_name} 'what' text missing from quick ref"
        assert ct.example in table, f"{tool_name} example missing from quick ref"


def test_legacy_model_family_hints_emit_portable_opencode_instructions() -> None:
    """FR03: legacy model-family hints are accepted but do not change core protocol."""
    outputs = []
    for family in ("qwen", "claude", "gpt", "generic"):
        renderer = ProtocolRenderer(
            client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
            model_family=family,
        )
        instructions = renderer.render_opencode_instructions()
        outputs.append(instructions)
        assert "# TRW Instructions" in instructions
        assert "project-native" in instructions
        assert "Nudge Policy" in instructions
        assert "/think" not in instructions
        assert "Qwen-Coder-Next" not in instructions
        assert "chain-of-thought" not in instructions
        assert "extended thinking" not in instructions.lower()

    assert len(set(outputs)) == 1


def test_ceremony_mode_switching_full_vs_minimal() -> None:
    """FR04: Verify renderer output changes with ceremony mode."""
    profile = ClientProfile(client_id="claude-code", display_name="claude-code")
    full_renderer = ProtocolRenderer(client_profile=profile, ceremony_mode="FULL")
    minimal_renderer = ProtocolRenderer(client_profile=profile, ceremony_mode="MINIMAL")

    full_output = full_renderer.render_behavioral_protocol()
    minimal_output = minimal_renderer.render_minimal_protocol()

    assert "Execution Phases" in full_output
    assert "Execution Phases" not in minimal_output
    assert "project-native checks" in minimal_output


def test_ceremony_mode_compact() -> None:
    """FR04: COMPACT mode includes quick-ref table but omits phases and flows."""
    profile = ClientProfile(client_id="claude-code", display_name="claude-code")
    compact_renderer = ProtocolRenderer(client_profile=profile, ceremony_mode="COMPACT")
    full_renderer = ProtocolRenderer(client_profile=profile, ceremony_mode="FULL")

    compact_output = compact_renderer.render_compact_protocol()
    full_output = full_renderer.render_behavioral_protocol()

    # COMPACT includes the quick-ref table
    assert "TRW Behavioral Protocol (Auto-Generated)" in compact_output
    assert "trw_session_start" in compact_output
    assert "trw_deliver" in compact_output

    # COMPACT omits detailed sections that FULL includes
    assert "Execution Phases" not in compact_output
    assert "Tool Lifecycle" not in compact_output
    assert "Example Flows" not in compact_output

    # COMPACT includes session boundary text
    assert "trw_session_start()" in compact_output

    # COMPACT is meaningfully shorter than FULL
    assert len(compact_output) < len(full_output) / 2


def test_render_phase_descriptions() -> None:
    """Verify phase descriptions include all 6 phases."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="claude-code", display_name="claude-code"))
    output = renderer.render_phase_descriptions()
    assert "RESEARCH" in output
    assert "DELIVER" in output
    assert "\u2192" in output


def test_render_ceremony_flows() -> None:
    """Verify ceremony flows include quick task and full run."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="claude-code", display_name="claude-code"))
    output = renderer.render_ceremony_flows()
    assert "Quick Task" in output
    assert "Full Run" in output
    assert "trw_deliver()" in output


def test_render_framework_reference_gated() -> None:
    """Verify framework reference is gated by client profile flag."""
    renderer_enabled = ProtocolRenderer(
        client_profile=ClientProfile(client_id="test", display_name="test", include_framework_ref=True)
    )
    renderer_disabled = ProtocolRenderer(
        client_profile=ClientProfile(client_id="test", display_name="test", include_framework_ref=False)
    )
    assert "FRAMEWORK.md" in renderer_enabled.render_framework_reference()
    assert renderer_disabled.render_framework_reference() == ""


def test_render_closing_reminder() -> None:
    """Verify closing reminder includes session boundary text."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test", display_name="test"))
    output = renderer.render_closing_reminder()
    assert "Session Boundaries" in output
    assert "trw_session_start()" in output


def test_render_behavioral_protocol_full() -> None:
    """FR04: FULL mode behavioral protocol includes all sections."""
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(
            client_id="claude-code",
            display_name="claude-code",
            include_framework_ref=True,
        ),
        ceremony_mode="FULL",
    )
    output = renderer.render_behavioral_protocol()
    assert "TRW Behavioral Protocol" in output
    assert "Execution Phases" in output
    assert "Tool Lifecycle" in output
    assert "Example Flows" in output
    assert "Framework Reference" in output
    assert "Session Boundaries" in output


def test_opencode_generic_fallback() -> None:
    """Verify unknown model family falls back to portable instructions."""
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family="unknown-model",
    )
    instructions = renderer.render_opencode_instructions()
    assert "TRW Instructions" in instructions
    assert "Model and Context Policy" in instructions
    assert "project-native" in instructions


# ---------------------------------------------------------------------------
# FR01: All generators delegate to ProtocolRenderer
# ---------------------------------------------------------------------------


def test_static_sections_delegate_to_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: _static_sections functions delegate to ProtocolRenderer."""
    # Patch get_config to return a profile
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.claude_md import _static_sections

    mock_config = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: mock_config)

    # Each function should produce non-empty output from the renderer
    assert "TRW Behavioral Protocol" in _static_sections.render_ceremony_quick_ref()
    assert "RESEARCH" in _static_sections.render_phase_descriptions()
    assert "Tool Lifecycle" in _static_sections.render_ceremony_table()
    assert "Quick Task" in _static_sections.render_ceremony_flows()


def test_opencode_sections_delegate_to_renderer() -> None:
    """FR01: _opencode_sections.render_opencode_instructions delegates to renderer."""
    from trw_mcp.state.claude_md._opencode_sections import render_opencode_instructions

    output = render_opencode_instructions("qwen")
    assert "# TRW Instructions" in output
    assert "Nudge Policy" in output
    assert "Qwen" not in output


# ---------------------------------------------------------------------------
# FR02: Ceremony table contains ALL CEREMONY_TOOLS entries
# ---------------------------------------------------------------------------


def test_ceremony_table_has_all_tools() -> None:
    """FR02: Every tool in CEREMONY_TOOLS appears in the rendered table."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test", display_name="test"))
    table = renderer.render_ceremony_table()
    for ct in CEREMONY_TOOLS:
        assert ct.tool in table, f"Tool '{ct.tool}' missing from ceremony table"


def test_phase_descriptions_has_all_phases() -> None:
    """FR02: All 6 phases appear in the phase descriptions output."""
    renderer = ProtocolRenderer(client_profile=ClientProfile(client_id="test", display_name="test"))
    output = renderer.render_phase_descriptions()
    for name, purpose in PHASE_DESCRIPTIONS:
        assert name in output, f"Phase '{name}' missing"
        assert purpose in output, f"Purpose for '{name}' missing"


# ---------------------------------------------------------------------------
# FR03: Portable model-family compatibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", ["qwen", "gpt", "claude", "generic", "unknown-llm"])
def test_opencode_model_family_content_is_portable(family: str) -> None:
    """FR03: Every model-family hint produces the same portable v25 protocol."""
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family=family,
    )
    output = renderer.render_opencode_instructions()
    assert "# TRW Instructions" in output
    assert "Model and Context Policy" in output
    assert "project-native" in output
    assert "Nudge Policy" in output
    assert "/think" not in output
    assert "chain-of-thought" not in output
    assert "extended thinking" not in output.lower()


def test_opencode_families_match() -> None:
    """FR03: Different legacy family hints no longer fork core protocol text."""
    profile = ClientProfile(client_id="opencode", display_name="opencode")
    outputs = {
        family: ProtocolRenderer(client_profile=profile, model_family=family).render_opencode_instructions()
        for family in ("qwen", "gpt", "claude", "generic")
    }
    assert len(set(outputs.values())) == 1


# ---------------------------------------------------------------------------
# DRY: SESSION_BOUNDARY_TEXT is canonical
# ---------------------------------------------------------------------------


def test_session_boundary_text_is_canonical() -> None:
    """SESSION_BOUNDARY_TEXT in _renderer.py is the single source of truth."""
    from trw_mcp.state.claude_md._static_sections import _SESSION_BOUNDARY_TEXT

    assert _SESSION_BOUNDARY_TEXT is SESSION_BOUNDARY_TEXT


# ---------------------------------------------------------------------------
# Legacy compat: platform= kwarg
# ---------------------------------------------------------------------------


def test_legacy_platform_kwarg() -> None:
    """Legacy: platform= kwarg creates a ClientProfile automatically."""
    renderer = ProtocolRenderer(platform="gemini")
    assert renderer.platform == "gemini"
    assert renderer.client_profile.client_id == "gemini"
