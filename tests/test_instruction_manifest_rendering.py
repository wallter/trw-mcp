"""Rendering and manifest shape tests for instruction manifest support."""

from __future__ import annotations

import pytest

from trw_mcp.models.surface_packs import KERNEL_TOOLS
from trw_mcp.state.claude_md._tool_manifest import (
    _ELIGIBLE_TOOLS,
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


def _strip_client_integration_appendix(output: str, client_id: str) -> str:
    """Remove the FR06 client-integration appendix (transport-loss + three-class
    capability listing) from a rendered section.

    The capability block (PRD-CORE-218 FR06, marker ``trw:capabilities``)
    legitimately NAMES discoverable/gated tools (e.g. ``trw_build_check``) as the
    capabilities an agent can request — that is a separate carrier from the
    task-independent tool LIST a section renders via ``render_tool_list``. These
    filter tests assert the LIST omits unexposed tools, so they strip the
    appendix first (it is emitted verbatim by ``render_client_integration_appendix``).
    """
    from trw_mcp.bootstrap._client_integration_appendix import (
        render_client_integration_appendix,
    )

    return output.replace(render_client_integration_appendix(client_id), "")


class TestToolDescriptions:
    """TOOL_DESCRIPTIONS covers all tools and is well-formed."""

    def test_covers_all_eligible_tools(self) -> None:
        """Every eligible (public) manifest tool has a description, and vice versa."""
        eligible = set(_ELIGIBLE_TOOLS)
        described = set(TOOL_DESCRIPTIONS)
        assert eligible == described, (
            f"Missing descriptions: {eligible - described}, Extra descriptions: {described - eligible}"
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
    """resolve_exposed_tools projects the task-independent instruction baseline
    of the CORE-218 authority (PRD-CORE-218 FR04)."""

    def test_all_mode(self) -> None:
        """'all' → the full eligible public surface."""
        assert resolve_exposed_tools("all") == set(_ELIGIBLE_TOOLS)

    def test_standard_mode_is_kernel_baseline(self) -> None:
        """'standard' (default) → the task-independent kernel baseline."""
        assert resolve_exposed_tools("standard") == set(KERNEL_TOOLS)

    def test_unknown_mode_falls_back_to_kernel(self) -> None:
        """Any non-'all' value degrades to the kernel baseline, never full."""
        assert resolve_exposed_tools("nonexistent") == set(KERNEL_TOOLS)


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
            # Strip the verbatim deliver-gate statement (QUAL-104 FR03 gate prose)
            # AND the FR06 capability appendix (which names discoverable/gated
            # tools) — both legitimately name tools outside the LIST. The
            # tool-LIST filter itself must omit unexposed tools.
            tool_list = _strip_client_integration_appendix(_strip_deliver_gate_block(output), "agents")
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
        # Strip the deliver-gate prose (QUAL-104 FR03) AND the FR06 capability
        # appendix; both legitimately name tools outside the filtered LIST.
        tool_list = _strip_client_integration_appendix(_strip_deliver_gate_block(output), "codex")
        assert "trw_build_check" not in tool_list


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

    def test_standard_mode(self) -> None:
        result = resolve_exposed_tools("standard")
        assert result == frozenset(KERNEL_TOOLS)


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
        # Strip the deliver-gate prose (QUAL-104 FR03) AND the FR06 capability
        # appendix; both legitimately name tools outside the filtered LIST.
        tool_list = _strip_client_integration_appendix(_strip_deliver_gate_block(output), "codex")
        assert "trw_build_check" not in tool_list
        assert "trw_recall" not in tool_list
