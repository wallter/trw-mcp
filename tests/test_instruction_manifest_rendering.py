"""Rendering and manifest shape tests for instruction manifest support."""

from __future__ import annotations

import pytest

from trw_mcp.models.tool_summaries import TOOL_SUMMARIES
from trw_mcp.state.claude_md._tool_manifest import (
    _ELIGIBLE_TOOLS,
    render_tool_list,
    resolve_exposed_tools,
)


class TestToolSummaries:
    """TOOL_SUMMARIES holds one well-formed summary per registered tool (PRD-INFRA-195-FR01)."""

    def test_covers_all_eligible_tools(self) -> None:
        """Every eligible (public) manifest tool has a summary, and vice versa."""
        eligible = set(_ELIGIBLE_TOOLS)
        described = set(TOOL_SUMMARIES)
        assert eligible == described, f"Missing summaries: {eligible - described}, Extra: {described - eligible}"

    def test_summaries_fit_the_length_budget(self) -> None:
        """NFR01: one line of at most 240 characters, a sentence ending in a full stop."""
        for tool, summary in TOOL_SUMMARIES.items():
            assert 5 < len(summary) <= 240, f"{tool}: {len(summary)} chars"
            assert "\n" not in summary and summary.endswith("."), f"{tool}: {summary!r}"

    def test_no_duplicate_summaries(self) -> None:
        """No two tools share the exact same summary."""
        seen: dict[str, str] = {}
        for tool, summary in TOOL_SUMMARIES.items():
            if summary in seen:
                pytest.fail(f"{tool} and {seen[summary]} share summary: {summary!r}")
            seen[summary] = tool


async def _served_descriptions() -> dict[str, str]:
    """What a client receives: every registered tool's description off the production app."""
    from tests._served_app import served_app

    mcp = served_app()
    served: dict[str, str] = {}
    for name in sorted(_ELIGIBLE_TOOLS):
        tool = await mcp.get_tool(name)  # type: ignore[attr-defined]
        served[name] = str(tool.to_mcp_tool().model_dump(by_alias=True)["description"])
    return served


class TestOneStringPerTool:
    """PRD-INFRA-195-FR01: the model, the instruction files and /docs/tools read one string."""

    async def test_served_description_opens_with_the_summary(self) -> None:
        served = await _served_descriptions()
        mismatched = {
            name: description.split("\n\n", 1)[0]
            for name, description in served.items()
            if description.split("\n\n", 1)[0] != TOOL_SUMMARIES[name]
        }
        assert mismatched == {}
        # The docstring body (Use when, output contract) still follows it.
        assert all("Use when" in description for description in served.values())

    async def test_client_list_tools_serves_the_same_description(self) -> None:
        """The in-memory client sees the registry's description, not a rewritten one."""
        from fastmcp import Client

        from tests._served_app import served_app

        mcp = served_app()
        served = await _served_descriptions()
        async with Client(mcp) as client:  # type: ignore[arg-type]
            listed = {tool.name: tool.description for tool in await client.list_tools()}
        assert listed, "no tools listed"
        assert {name: listed[name] for name in listed} == {name: served[name] for name in listed}

    def test_instruction_lines_are_the_summaries(self) -> None:
        lines = render_tool_list(None).splitlines()
        assert [line.split(" — ", 1)[1] for line in lines] == list(TOOL_SUMMARIES.values())

    async def test_reapplying_at_boot_does_not_prefix_twice(self) -> None:
        from tests._served_app import served_app

        mcp = served_app()
        from trw_mcp.server._tool_summaries import apply_tool_summaries

        before = await _served_descriptions()
        assert set(await apply_tool_summaries(mcp)) == set(TOOL_SUMMARIES)  # type: ignore[arg-type]
        assert await _served_descriptions() == before

    def test_served_description_joins_summary_and_body(self) -> None:
        from trw_mcp.server._tool_summaries import served_description

        assert served_description("One.", "  Use when x.\n") == "One.\n\nUse when x."
        assert served_description("One.", None) == "One."


class TestResolveExposedTools:
    """resolve_exposed_tools projects the task-independent instruction baseline
    of the CORE-218 authority (PRD-CORE-218 FR04)."""

    def test_all_mode(self) -> None:
        """'all' → the full eligible public surface."""
        assert resolve_exposed_tools("all") == set(_ELIGIBLE_TOOLS)

    def test_standard_mode_is_kernel_plus_the_never_hide_set(self) -> None:
        """'standard' (default) -> ALWAYS_ON_TOOLS (PRD-CORE-300 S11b).

        The surface is flat now: every tool no config flag gates (the kernel
        plus every always-on pack) is callable in every agent session,
        whatever the task.
        """
        from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS, PACK_TOOLS

        assert resolve_exposed_tools("standard") == set(ALWAYS_ON_TOOLS)
        # The REGISTERED kernel (trw_code is pending, so excluded) is a subset
        # of the always-on baseline.
        assert set(PACK_TOOLS["kernel"]) <= set(ALWAYS_ON_TOOLS)

    def test_unknown_mode_falls_back_to_the_same_baseline(self) -> None:
        """Any non-'all' value degrades to the agent baseline, never full."""
        from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS

        assert resolve_exposed_tools("nonexistent") == set(ALWAYS_ON_TOOLS)


class TestRenderToolList:
    """render_tool_list filters by exposed_tools."""

    def test_none_renders_all(self) -> None:
        """exposed_tools=None renders all tools (backward compat)."""
        output = render_tool_list(None)
        for tool_name in TOOL_SUMMARIES:
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


class TestAgentsSectionNamesNoGatedTool:
    """PRD-CORE-135 FR01's property after PRD-CORE-301-FR13: the block describes no tool a session may lack.

    FR13 replaced the block's filtered tool list with a pointer to the live
    surface, so the shared block names no flag-gated tool at all — under any
    config — and a session learns what it has from ``trw_status(detail="surface")``.
    """

    @pytest.mark.parametrize("tool", ["trw_dispatch", "trw_assess", "trw_send", "trw_inbox"])
    def test_the_block_never_names_a_flag_gated_tool(self, tool: str) -> None:
        from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

        assert tool not in render_agents_trw_section()

    def test_the_block_names_the_kernel_tools_its_rules_bind(self) -> None:
        from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

        output = render_agents_trw_section()
        for tool in (
            "trw_session_start",
            "trw_checkpoint",
            "trw_learn",
            "trw_recall",
            "trw_deliver",
            "trw_build_check",
        ):
            assert tool in output
        assert 'trw_status(detail="surface")' in output


class TestResolveExposedToolsFrozenset:
    """resolve_exposed_tools returns frozenset (immutable)."""

    def test_returns_frozenset(self) -> None:
        result = resolve_exposed_tools("all")
        assert isinstance(result, frozenset)

    def test_standard_mode(self) -> None:
        from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS

        result = resolve_exposed_tools("standard")
        assert result == frozenset(ALWAYS_ON_TOOLS)
