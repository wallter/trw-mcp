"""Tests for TOOL_PRESETS constants and tool exposure groups (PRD-CORE-125 FR12).

Verifies:
- Preset sizes and composition
- Group disjointness (no tool in two groups)
- Standard preset includes core + memory
- All preset names are valid
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config._defaults import (
    TOOL_GROUP_ADMIN,
    TOOL_GROUP_CODE_INTEL,
    TOOL_GROUP_CORE,
    TOOL_GROUP_EVIDENCE,
    TOOL_GROUP_MEMORY,
    TOOL_GROUP_OBSERVABILITY,
    TOOL_GROUP_PHASE_CONTROL,
    TOOL_GROUP_PROBE,
    TOOL_GROUP_QUALITY,
    TOOL_PRESETS,
)

# All tool groups that compose the "all" preset (round-2 transport e2e F1/F3:
# the preset must reconcile with the registered surface).
_ALL_PRESET_GROUPS = (
    TOOL_GROUP_CORE,
    TOOL_GROUP_MEMORY,
    TOOL_GROUP_QUALITY,
    TOOL_GROUP_OBSERVABILITY,
    TOOL_GROUP_ADMIN,
    TOOL_GROUP_PROBE,
    TOOL_GROUP_PHASE_CONTROL,
    TOOL_GROUP_CODE_INTEL,
    TOOL_GROUP_EVIDENCE,
)


@pytest.mark.unit
def test_preset_core_has_4_tools() -> None:
    """TOOL_PRESETS['core'] contains exactly the 4 lifecycle tools."""
    assert len(TOOL_PRESETS["core"]) == 4
    assert set(TOOL_PRESETS["core"]) == {
        "trw_session_start",
        "trw_checkpoint",
        "trw_learn",
        "trw_deliver",
    }


@pytest.mark.unit
def test_preset_minimal_has_6_tools() -> None:
    """TOOL_PRESETS['minimal'] = core(4) + memory(2) = 6 tools."""
    assert len(TOOL_PRESETS["minimal"]) == 6


@pytest.mark.unit
def test_preset_standard_includes_core_and_memory() -> None:
    """TOOL_PRESETS['standard'] is a superset of core and memory tools."""
    standard_set = set(TOOL_PRESETS["standard"])
    assert set(TOOL_GROUP_CORE).issubset(standard_set)
    assert set(TOOL_GROUP_MEMORY).issubset(standard_set)


@pytest.mark.unit
def test_preset_standard_includes_status_and_init() -> None:
    """TOOL_PRESETS['standard'] includes trw_status and trw_init convenience tools."""
    standard_set = set(TOOL_PRESETS["standard"])
    assert "trw_status" in standard_set
    assert "trw_init" in standard_set


@pytest.mark.unit
def test_preset_all_has_all_group_tools() -> None:
    """TOOL_PRESETS['all'] is the union of all groups (incl. PRD-CORE-144 probe,
    phase-control, code-intel, and evidence bridges)."""
    all_groups: set[str] = set()
    for g in _ALL_PRESET_GROUPS:
        all_groups |= set(g)
    assert set(TOOL_PRESETS["all"]) == all_groups


@pytest.mark.unit
def test_preset_all_includes_probe_group() -> None:
    """F-12: the probe tools are surfaced in the 'all' preset (not silently lost)."""
    assert set(TOOL_GROUP_PROBE) <= set(TOOL_PRESETS["all"])
    assert "trw_probe" in TOOL_PRESETS["all"]
    assert "trw_probe_budget_status" in TOOL_PRESETS["all"]


@pytest.mark.unit
def test_preset_all_tool_count() -> None:
    """TOOL_PRESETS['all'] = union of all groups (groups are disjoint, so the
    flat length equals the sum of group lengths)."""
    expected = sum(len(g) for g in _ALL_PRESET_GROUPS)
    assert len(TOOL_PRESETS["all"]) == expected


@pytest.mark.unit
def test_preset_groups_no_overlap() -> None:
    """No tool appears in more than one group across ALL preset groups."""
    all_tools: list[str] = []
    for g in _ALL_PRESET_GROUPS:
        all_tools.extend(g)
    # If no overlap, the set size equals the list length
    assert len(set(all_tools)) == len(all_tools), "Tool groups have overlapping entries"


@pytest.mark.unit
def test_all_preset_keys_are_valid() -> None:
    """All TOOL_PRESETS keys are valid exposure mode values."""
    valid_modes = {"core", "minimal", "standard", "all"}
    assert set(TOOL_PRESETS.keys()) == valid_modes


@pytest.mark.unit
def test_presets_are_tuples() -> None:
    """All TOOL_PRESETS values are tuples (immutable)."""
    for name, preset in TOOL_PRESETS.items():
        assert isinstance(preset, tuple), f"Preset '{name}' is {type(preset).__name__}, expected tuple"


@pytest.mark.unit
def test_all_tool_names_start_with_trw() -> None:
    """Every tool in every group starts with 'trw_' prefix."""
    for preset in TOOL_PRESETS.values():
        for tool in preset:
            assert tool.startswith("trw_"), f"Tool {tool!r} missing trw_ prefix"


def _registered_production_tools() -> set[str]:
    """Return the full set of tool names registered on a FRESH production server.

    Mirrors ``server/_tools._register_tools`` (without the exposure filter) so
    the parity test sees every registered tool, not the post-filter subset.
    """
    import asyncio

    from fastmcp import FastMCP

    from trw_mcp.tools._pipeline_health_tool import register_pipeline_health_tools
    from trw_mcp.tools.agent_work_evidence import register_agent_work_evidence_tools
    from trw_mcp.tools.before_edit_hint import register_before_edit_hint_tools
    from trw_mcp.tools.before_edit_hint_batch import register_before_edit_hint_batch_tools
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.ceremony_feedback import register_ceremony_feedback_tools
    from trw_mcp.tools.channel_render import register_channel_render_tools
    from trw_mcp.tools.channel_stats import register_channel_stats_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.code_index import register_code_index_tools
    from trw_mcp.tools.code_search import register_code_search_tools
    from trw_mcp.tools.codebase_risk_report import register_codebase_risk_report_tools
    from trw_mcp.tools.cross_repo_ordering import register_cross_repo_ordering_tools
    from trw_mcp.tools.entity_risk_map import register_entity_risk_map_tools
    from trw_mcp.tools.knowledge import register_knowledge_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.mcp_security_status import register_mcp_security_status
    from trw_mcp.tools.meta_tune_ops import register_meta_tune_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.ordering_compare import register_ordering_compare_tools
    from trw_mcp.tools.phase_overrides import register_phase_override_tools
    from trw_mcp.tools.query_tools import register_query_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.review import register_review_tools
    from trw_mcp.tools.skill_discovery import register_skill_discovery_tools
    from trw_mcp.tools.submit_feedback import register_submit_feedback_tools
    from trw_mcp.tools.trw_probe import register_probe_tools
    from trw_mcp.tools.trw_profile_explain import register_trw_profile_explain_tools

    server = FastMCP("parity-probe")
    for fn in (
        register_build_tools,
        register_ceremony_tools,
        register_ceremony_feedback_tools,
        register_checkpoint_tools,
        register_learning_tools,
        register_meta_tune_tools,
        register_knowledge_tools,
        register_orchestration_tools,
        register_requirements_tools,
        register_review_tools,
        register_query_tools,
        register_mcp_security_status,
        register_before_edit_hint_tools,
        register_before_edit_hint_batch_tools,
        register_codebase_risk_report_tools,
        register_ordering_compare_tools,
        register_cross_repo_ordering_tools,
        register_code_index_tools,
        register_code_search_tools,
        register_entity_risk_map_tools,
        register_agent_work_evidence_tools,
        register_skill_discovery_tools,
        register_submit_feedback_tools,
        register_channel_render_tools,
        register_channel_stats_tools,
        register_pipeline_health_tools,
        register_probe_tools,
        register_trw_profile_explain_tools,
        register_phase_override_tools,
    ):
        fn(server)
    return {t.name for t in asyncio.run(server.list_tools())}


def test_tool_preset_bridge_parity() -> None:
    """Round-2 transport e2e F1/F3: every tool REGISTERED on the production
    server must be reachable through the preset+allowlist first-party bridge
    (``TOOL_PRESETS['all']``) OR named in the explicit
    ``INTENTIONALLY_UNBRIDGED_TOOLS`` exclusion list.

    This fails CI when a NEW registered tool is silently unbridged — the exact
    drift that left ~19 of 43 tools invisible / un-callable over real transport
    (``tool_not_in_server_capabilities``). The fix for a new tool is a DELIBERATE
    choice: add it to a tool group OR document the exclusion — never let it drift.
    """
    from trw_mcp.models.config._defaults import (
        INTENTIONALLY_UNBRIDGED_TOOLS,
        TOOL_PRESETS,
    )

    registered = _registered_production_tools()
    bridged = set(TOOL_PRESETS["all"])
    excluded = set(INTENTIONALLY_UNBRIDGED_TOOLS)

    unaccounted = registered - bridged - excluded
    assert not unaccounted, (
        "Registered tools neither bridged via TOOL_PRESETS['all'] nor in "
        f"INTENTIONALLY_UNBRIDGED_TOOLS: {sorted(unaccounted)}. Either add each "
        "to a tool group in _defaults.py (so the first-party bridge reaches it) "
        "or document the intentional exclusion."
    )

    # The exclusion list must not name phantom tools (drift the other way):
    # everything excluded must actually be registered.
    phantom_exclusions = excluded - registered
    assert not phantom_exclusions, (
        f"INTENTIONALLY_UNBRIDGED_TOOLS names tools not registered: {sorted(phantom_exclusions)}"
    )

    # A bridged tool must never also be in the exclusion list.
    assert not (bridged & excluded), "A tool is both bridged and excluded — contradictory policy"
