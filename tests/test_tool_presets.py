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
    TOOL_GROUP_CORE,
    TOOL_GROUP_MEMORY,
    TOOL_GROUP_OBSERVABILITY,
    TOOL_GROUP_QUALITY,
    TOOL_PRESETS,
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
def test_preset_minimal_has_7_tools() -> None:
    """TOOL_PRESETS['minimal'] = core(4) + memory(3) = 7 tools."""
    assert len(TOOL_PRESETS["minimal"]) == 7


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
    """TOOL_PRESETS['all'] is the union of all 5 groups."""
    all_groups = set(TOOL_GROUP_CORE) | set(TOOL_GROUP_MEMORY) | set(TOOL_GROUP_QUALITY) | set(TOOL_GROUP_OBSERVABILITY) | set(TOOL_GROUP_ADMIN)
    assert set(TOOL_PRESETS["all"]) == all_groups


@pytest.mark.unit
def test_preset_all_tool_count() -> None:
    """TOOL_PRESETS['all'] has 4+3+5+6+7 = 25 tools."""
    expected = (
        len(TOOL_GROUP_CORE)
        + len(TOOL_GROUP_MEMORY)
        + len(TOOL_GROUP_QUALITY)
        + len(TOOL_GROUP_OBSERVABILITY)
        + len(TOOL_GROUP_ADMIN)
    )
    assert len(TOOL_PRESETS["all"]) == expected


@pytest.mark.unit
def test_preset_groups_no_overlap() -> None:
    """No tool appears in more than one group (core, memory, quality, observability, admin)."""
    groups = [
        TOOL_GROUP_CORE,
        TOOL_GROUP_MEMORY,
        TOOL_GROUP_QUALITY,
        TOOL_GROUP_OBSERVABILITY,
        TOOL_GROUP_ADMIN,
    ]
    all_tools: list[str] = []
    for g in groups:
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
