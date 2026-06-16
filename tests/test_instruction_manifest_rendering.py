"""Rendering and manifest shape tests for instruction manifest support."""

from __future__ import annotations

import pytest

from trw_mcp.models.config._defaults import TOOL_PRESETS
from trw_mcp.state.claude_md._tool_manifest import (
    TOOL_DESCRIPTIONS,
    ToolEntry,
    render_tool_list,
    resolve_exposed_tools,
)


def _strip_deliver_gate_block(output: str) -> str:
    """Remove the canonical deliver-gate statement block from a rendered section.

    The deliver-gate statement (QUAL-104 FR03) is the protocol carrier: it
    legitimately names the rigid ceremony tools (e.g. ``trw_build_check``) as
    part of the Constitution gate prose, and MUST keep naming them so the gate
    text stays verbatim across every light-client surface. That gate prose is
    distinct from the *tool list* a tool-exposure preset filters — the filter
    contract only governs the tool LIST, not the gate prose.

    Tool-filtering assertions therefore strip this block (emitted verbatim by
    ``render_deliver_gate_statement()``, anchored by its ``trw:lifecycle-sync``
    marker) before asserting an excluded tool is absent. This preserves the
    tests' real intent (filtered tool LISTS must omit excluded tools) without
    falsely flagging the load-bearing gate prose.
    """
    from trw_mcp.state.claude_md.sections._tool_lifecycle import (
        render_deliver_gate_statement,
    )

    gate_block = render_deliver_gate_statement()
    return output.replace(gate_block, "")


class TestToolDescriptions:
    """TOOL_DESCRIPTIONS covers all tools and is well-formed."""

    def test_covers_all_preset_tools(self) -> None:
        """Every tool in TOOL_PRESETS['all'] has a description."""
        all_tools = set(TOOL_PRESETS["all"])
        described = set(TOOL_DESCRIPTIONS)
        assert all_tools == described, (
            f"Missing descriptions: {all_tools - described}, Extra descriptions: {described - all_tools}"
        )

    def test_descriptions_are_nonempty_strings(self) -> None:
        """Every description is a non-empty string."""
        for tool, desc in TOOL_DESCRIPTIONS.items():
            assert isinstance(desc, str), f"{tool}: description is not a string"
            assert len(desc) > 5, f"{tool}: description too short: {desc!r}"

    def test_no_duplicate_descriptions(self) -> None:
        """No two tools share the exact same description."""
        seen: dict[str, str] = {}
        for tool, desc in TOOL_DESCRIPTIONS.items():
            if desc in seen:
                pytest.fail(f"{tool} and {seen[desc]} share description: {desc!r}")
            seen[desc] = tool


class TestResolveExposedTools:
    """resolve_exposed_tools returns the correct set for each mode."""

    def test_all_mode(self) -> None:
        result = resolve_exposed_tools("all")
        assert result == set(TOOL_PRESETS["all"])

    def test_core_mode(self) -> None:
        result = resolve_exposed_tools("core")
        assert result == set(TOOL_PRESETS["core"])

    def test_minimal_mode(self) -> None:
        result = resolve_exposed_tools("minimal")
        assert result == set(TOOL_PRESETS["minimal"])

    def test_custom_mode(self) -> None:
        custom = ["trw_learn", "trw_deliver"]
        result = resolve_exposed_tools("custom", custom_list=custom)
        assert result == {"trw_learn", "trw_deliver"}

    def test_unknown_mode_falls_back_to_all(self) -> None:
        result = resolve_exposed_tools("nonexistent")
        assert result == set(TOOL_PRESETS["all"])


class TestRenderToolList:
    """render_tool_list filters by exposed_tools."""

    def test_none_renders_all(self) -> None:
        """exposed_tools=None renders all tools (backward compat)."""
        output = render_tool_list(None)
        for tool_name in TOOL_DESCRIPTIONS:
            assert tool_name in output

    def test_subset_omits_unexposed(self) -> None:
        """Only listed tools appear when exposed_tools is a subset."""
        exposed = {"trw_session_start", "trw_learn"}
        output = render_tool_list(exposed)
        assert "trw_session_start" in output
        assert "trw_learn" in output
        assert "trw_deliver" not in output
        assert "trw_build_check" not in output

    def test_empty_set_returns_empty(self) -> None:
        """Empty exposed set produces no output."""
        output = render_tool_list(set())
        assert output == ""


class TestConditionalSectionRendering:
    """render_agents_trw_section and render_codex_trw_section filter tools."""

    def test_agents_section_none_renders_all(self) -> None:
        """exposed_tools=None includes all tools."""
        from unittest.mock import patch

        with patch(
            "trw_mcp.state.claude_md._static_sections._load_analytics_counts",
            return_value=(10, 50),
        ):
            from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

            output = render_agents_trw_section(exposed_tools=None)
            assert "trw_session_start" in output
            assert "trw_deliver" in output
            assert "trw_build_check" in output

    def test_agents_section_filters_tools(self) -> None:
        """Only exposed tools appear in the rendered section."""
        from unittest.mock import patch

        with patch(
            "trw_mcp.state.claude_md._static_sections._load_analytics_counts",
            return_value=(10, 50),
        ):
            from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

            exposed = {"trw_session_start", "trw_learn"}
            output = render_agents_trw_section(exposed_tools=exposed)
            assert "trw_session_start" in output
            assert "trw_learn" in output
            # Strip the verbatim deliver-gate statement: it legitimately names
            # rigid tools in the Constitution gate prose (QUAL-104 FR03). The
            # tool-LIST filter must not contain them — the gate prose may.
            tool_list = _strip_deliver_gate_block(output)
            assert "trw_build_check" not in tool_list
            assert "trw_recall" not in tool_list

    def test_codex_section_none_renders_all(self) -> None:
        """Codex section with None includes all tools."""
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        output = render_codex_trw_section(exposed_tools=None)
        assert "trw_session_start" in output
        assert "trw_deliver" in output

    def test_codex_section_filters_tools(self) -> None:
        """Codex section with subset omits unexposed tools."""
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        exposed = {"trw_session_start", "trw_checkpoint"}
        output = render_codex_trw_section(exposed_tools=exposed)
        assert "trw_session_start" in output
        assert "trw_checkpoint" in output
        # The deliver-gate statement legitimately names rigid tools in its
        # Constitution gate prose (QUAL-104 FR03); strip it so the assertion
        # checks the tool LIST, not the gate prose.
        assert "trw_build_check" not in _strip_deliver_gate_block(output)


class TestToolEntry:
    """ToolEntry NamedTuple is well-formed."""

    def test_tool_entry_fields(self) -> None:
        entry = ToolEntry(name="trw_learn", description="Record discoveries")
        assert entry.name == "trw_learn"
        assert entry.description == "Record discoveries"

    def test_tool_entry_immutable(self) -> None:
        entry = ToolEntry(name="trw_learn", description="desc")
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]


class TestResolveExposedToolsFrozenset:
    """resolve_exposed_tools returns frozenset (immutable)."""

    def test_returns_frozenset(self) -> None:
        result = resolve_exposed_tools("all")
        assert isinstance(result, frozenset)

    def test_custom_returns_frozenset(self) -> None:
        result = resolve_exposed_tools("custom", custom_list=["trw_learn"])
        assert isinstance(result, frozenset)

    def test_standard_mode(self) -> None:
        result = resolve_exposed_tools("standard")
        assert result == frozenset(TOOL_PRESETS["standard"])


class TestAgentsSectionToolFiltering:
    """Verify render_agents_trw_section truly excludes unexposed tools."""

    def test_session_start_only_excludes_build_check_from_tool_list(self) -> None:
        """When only trw_session_start is exposed, tool list omits others."""
        from unittest.mock import patch

        with patch(
            "trw_mcp.state.claude_md._static_sections._load_analytics_counts",
            return_value=(5, 20),
        ):
            from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

            output = render_agents_trw_section(exposed_tools={"trw_session_start"})
            assert "`trw_session_start()`" in output
            assert "`trw_build_check()`" not in output
            assert "`trw_review()`" not in output
            assert "`trw_recall()`" not in output

    def test_codex_section_filters_tools(self) -> None:
        """render_codex_trw_section with subset omits unexposed tools."""
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        exposed = {"trw_session_start", "trw_deliver"}
        output = render_codex_trw_section(exposed_tools=exposed)
        assert "trw_session_start" in output
        assert "trw_deliver" in output
        # The deliver-gate statement legitimately names rigid tools in its
        # Constitution gate prose (QUAL-104 FR03); strip it so the assertion
        # checks the tool LIST, not the gate prose.
        tool_list = _strip_deliver_gate_block(output)
        assert "trw_build_check" not in tool_list
        assert "trw_recall" not in tool_list
