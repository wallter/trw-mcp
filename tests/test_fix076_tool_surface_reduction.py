"""PRD-FIX-076: MCP tool surface reduction — absence tests.

Verifies the 4 surviving low-signal tools are deregistered from the MCP
surface (FR01), dropped from the ADMIN group + manifest (FR02), and confirms
the 6 already-removed tools stay absent (FR00 no-op gate).

The underlying state logic (``state.ceremony_feedback`` /
``state.knowledge_topology``) is load-bearing and is NOT removed — this file
only asserts the *registered tool wrappers* are gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# The 4 surviving tools removed by FIX-076 (had live @server.tool decorators).
_REMOVED_TOOLS: frozenset[str] = frozenset(
    {
        "trw_ceremony_status",
        "trw_ceremony_approve",
        "trw_ceremony_revert",
        "trw_knowledge_sync",
    }
)

# FR00 no-op gate: these 6 were already gone at HEAD; they must STAY absent and
# no removal code is written for them.
_ALREADY_REMOVED_TOOLS: frozenset[str] = frozenset(
    {
        "trw_run_report",
        "trw_analytics_report",
        "trw_usage_report",
        "trw_trust_level",
        "trw_progressive_expand",
        "trw_quality_dashboard",
    }
)


async def test_removed_tools_absent_from_prod_server() -> None:
    """FR01: none of the 4 removed tools are registered on the production server."""
    from trw_mcp.server._app import mcp

    tools = await mcp._list_tools()
    tool_names = {t.name for t in tools}
    leaked = _REMOVED_TOOLS & tool_names
    assert not leaked, f"FIX-076 removed tools still registered: {sorted(leaked)}"


async def test_six_already_removed_tools_stay_absent() -> None:
    """FR00 no-op gate: the 6 already-removed tools remain absent from the surface."""
    from trw_mcp.server._app import mcp

    tools = await mcp._list_tools()
    tool_names = {t.name for t in tools}
    leaked = _ALREADY_REMOVED_TOOLS & tool_names
    assert not leaked, f"Already-removed tools unexpectedly present: {sorted(leaked)}"


def test_ceremony_feedback_module_has_no_tool_functions() -> None:
    """FR01: the ceremony-feedback tool wrapper functions are removed."""
    from trw_mcp.tools import ceremony_feedback

    for name in ("trw_ceremony_status", "trw_ceremony_approve", "trw_ceremony_revert"):
        assert not hasattr(ceremony_feedback, name), f"{name} wrapper still present"


def test_knowledge_module_has_no_tool_function() -> None:
    """FR01: the knowledge-sync tool wrapper function is removed."""
    from trw_mcp.tools import knowledge

    assert not hasattr(knowledge, "trw_knowledge_sync"), "trw_knowledge_sync wrapper still present"


def test_knowledge_sync_absent_from_capability_packs() -> None:
    """FR02: trw_knowledge_sync is not a member of any CORE-218 capability pack.

    (The CORE-125 TOOL_GROUP_ADMIN/TOOL_PRESETS vocabulary was removed when the
    kernel/pack resolver became the sole exposure authority.)"""
    from trw_mcp.models.surface_packs import PACK_TOOLS

    for pack_name, tools in PACK_TOOLS.items():
        assert "trw_knowledge_sync" not in tools, f"present in pack {pack_name!r}"


def test_removed_tools_absent_from_manifest() -> None:
    """FR02: manifest excludes all 4 removed tools and stale descriptions cannot linger."""
    from trw_mcp.state.claude_md._tool_manifest import _ELIGIBLE_TOOLS, TOOL_DESCRIPTIONS

    for name in _REMOVED_TOOLS:
        assert name not in TOOL_DESCRIPTIONS, f"{name} still described in manifest"
    # FIX-076's invariant is the ABSENCE of the removed tools above. Exact
    # surface size is owned by the CORE-218 manifest parity test
    # (test_tool_presets.py). Assert structural sanity only: every described
    # tool is in the eligible public surface, so stale descriptions cannot linger.
    stale = set(TOOL_DESCRIPTIONS) - set(_ELIGIBLE_TOOLS)
    assert not stale, f"manifest describes non-eligible tools: {sorted(stale)}"


def test_internal_state_logic_importable_by_consumers() -> None:
    """NFR01: the underlying state APIs other modules consume stay importable.

    Dead-code detection only — proves the symbols the deliver pipeline +
    telemetry steps import still resolve after the tool wrappers were removed.
    Behavioral coverage of these APIs lives in
    ``test_internal_state_logic_behaves`` (an integration test, since the real
    calls touch the filesystem).
    """
    from trw_mcp.state.ceremony_feedback import (  # noqa: F401
        approve_proposal,
        get_ceremony_status,
        revert_change,
    )
    from trw_mcp.state.knowledge_topology import execute_knowledge_sync  # noqa: F401
    from trw_mcp.state.memory_adapter import backfill_graph  # noqa: F401


@pytest.mark.integration
def test_internal_state_logic_behaves(tmp_path: Path) -> None:
    """F3: the preserved state APIs actually EXECUTE their logic, not just exist.

    Replaces the prior ``assert callable(...)`` existence checks (testing.md
    anti-pattern #3) with minimal behavioral calls that exercise real branches:
    return-structure, dry-run threshold, and the documented ValueError paths.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.ceremony_feedback import (
        approve_proposal,
        get_ceremony_status,
        revert_change,
    )
    from trw_mcp.state.knowledge_topology import execute_knowledge_sync
    from trw_mcp.state.memory_adapter import backfill_graph

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    # get_ceremony_status returns the documented {"task_classes": [...]} shape,
    # one entry per TaskClass when no specific class is requested.
    status = get_ceremony_status(trw_dir)
    assert isinstance(status, dict)
    task_classes = status["task_classes"]
    assert isinstance(task_classes, list)
    assert task_classes  # at least one TaskClass surfaced
    assert all("current_tier" in tc for tc in task_classes)

    # An invalid task_class is rejected (real validation branch, not a stub).
    with pytest.raises(ValueError):
        get_ceremony_status(trw_dir, task_class="not-a-real-class")

    # approve_proposal raises for an unknown id (in-memory lookup branch).
    with pytest.raises(ValueError):
        approve_proposal(trw_dir, "no-such-proposal")

    # revert_change raises for an unknown change_id (history-scan branch).
    with pytest.raises(ValueError):
        revert_change(trw_dir, "no-such-change")

    # execute_knowledge_sync dry-run returns a threshold-aware base result with
    # no writes (an empty store is below threshold -> threshold_met False).
    sync = execute_knowledge_sync(trw_dir, get_config(), dry_run=True)
    assert isinstance(sync, dict)
    assert sync["threshold_met"] is False

    # backfill_graph returns its int-valued counter dict on an empty store.
    counts = backfill_graph(trw_dir, limit=0)
    assert isinstance(counts, dict)
    assert all(isinstance(v, int) for v in counts.values())
