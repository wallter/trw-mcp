"""Typed reflection follow-through lifecycle coverage."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.requirements import ReflectionActionState
from trw_mcp.state.reflection_followthrough import derive_reflection_action_state


def test_prd_qual_120_fr06(tmp_path: Path) -> None:
    """Only a live implemented target closes approved reflection debt."""
    prds = tmp_path / "prds"
    prds.mkdir()

    def write_target(status: str, level: str = "") -> None:
        level_line = f"\n  functionality_level: {level}" if level else ""
        (prds / "PRD-CORE-070.md").write_text(
            f"---\nprd:\n  id: PRD-CORE-070\n  title: T\n  status: {status}{level_line}\n---\n",
            encoding="utf-8",
        )

    write_target("draft")
    routed = derive_reflection_action_state("act-1", "approved", "PRD-CORE-070", prds)
    assert routed.state is ReflectionActionState.ROUTED
    assert routed.debt_open and routed.reason == "target_not_implemented"

    write_target("implemented", "partial")
    implementing = derive_reflection_action_state("act-1", "routed", "PRD-CORE-070", prds)
    assert implementing.state is ReflectionActionState.IMPLEMENTING
    assert implementing.debt_open and implementing.reason == "target_implemented_but_not_live"

    reclaimed = derive_reflection_action_state("act-1", "verified_closed", "PRD-CORE-070", prds)
    assert reclaimed.debt_open

    write_target("implemented", "live")
    closed = derive_reflection_action_state("act-1", "routed", "PRD-CORE-070", prds)
    assert closed.state is ReflectionActionState.VERIFIED_CLOSED and not closed.debt_open

    missing = derive_reflection_action_state("act-2", "approved", "PRD-CORE-999", prds)
    assert missing.debt_open and missing.reason == "target_missing"
    unrouted = derive_reflection_action_state("act-3", "approved", "", prds)
    assert unrouted.debt_open and unrouted.reason == "approved_without_target"
    assert not derive_reflection_action_state("a", "rejected", "", prds).debt_open
    assert not derive_reflection_action_state("a", "proposed", "", prds).debt_open


def test_prd_qual_120_nfr04(tmp_path) -> None:
    """NFR04 acceptance: Large fixtures terminate without hiding skipped
    targets — reconciliation exposes counts and truncation and reads no
    unrelated artifact trees."""
    import pytest

    from trw_mcp.state.reflection_followthrough import (
        reconcile_debt,
        reconcile_debt_bounded,
    )

    prds = tmp_path / "prds"
    prds.mkdir()
    (prds / "PRD-CORE-901.md").write_text(
        "---\nprd:\n  id: PRD-CORE-901\n  title: T\n  status: implemented\nfunctionality_level: live\n---\n",
        encoding="utf-8",
    )
    # An unrelated artifact tree that bounded reconciliation must never read:
    # a PRD-shaped file that would crash frontmatter parsing if opened.
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "junk.md").write_bytes(b"\xff\xfe not yaml")

    actions = [
        {"action_id": f"act-{index:03d}", "state": "approved", "target_prd": "PRD-CORE-901"} for index in range(7)
    ]

    report = reconcile_debt_bounded(actions, prds, max_actions=5)
    # Counts + truncation are EXPOSED, skipped targets are named, not hidden.
    assert report["total_actions"] == 7
    assert report["evaluated_count"] == 5
    assert report["truncated"] is True
    assert report["skipped_actions"] == ["act-005", "act-006"]
    assert len(report["open"]) + len(report["closed"]) == 5

    # Under the bound: everything evaluates, truncated=False.
    full = reconcile_debt_bounded(actions, prds, max_actions=500)
    assert full["truncated"] is False and full["evaluated_count"] == 7
    assert full["skipped_actions"] == []

    # The FR07 wrapper keeps its signature and shares the bound.
    open_debt, closed = reconcile_debt(actions, prds, max_actions=5)
    assert len(open_debt) + len(closed) == 5

    # A non-positive bound is a typed usage error, never an unbounded scan.
    with pytest.raises(ValueError, match="max_actions must be positive"):
        reconcile_debt_bounded(actions, prds, max_actions=0)
