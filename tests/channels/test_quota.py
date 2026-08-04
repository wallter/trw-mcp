"""The channel tier ladder, and the quota enforcement removed with its callers.

``_quota.py`` implemented the one-pass tier-down quota loop for channel distill
segments (PRD-DIST-2400 Phase C / FR13). Commit ``b5d104f080`` removed the 12
instruction-file injection channels — every caller — and left the enforcement
code behind, still exported from ``channels/__init__`` and still covered by this
file's 18 unit tests. Full coverage on unreachable code is the most expensive
kind of green: it makes an orphan look maintained.

These tests replace those. They assert the ladder that IS consumed, and they
assert the removal stayed removed, because a deletion cannot be attributed by
"a test goes red" the way a behaviour fix can.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from trw_mcp.channels._quota import TIER_DOWN_LADDER

_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp"

#: Removed 2026-07-30 with their last callers. Re-adding any of these without a
#: production consumer recreates the orphan.
_REMOVED = ("check_quota", "enforce_quota_with_tier_down", "tier_down", "tier_index")


@pytest.mark.unit
def test_ladder_is_ordered_best_to_worst() -> None:
    """The ordering is the contract `_throttle` reverses; pin it, not just its length."""
    assert TIER_DOWN_LADDER == ("T4", "T3", "T2", "T1", "T0")


@pytest.mark.unit
def test_throttle_derives_its_ladder_from_this_one() -> None:
    """The single consumer must keep deriving, not hand-copy.

    ``_throttle`` needs ascending order and holds ``list(reversed(...))``. If it
    ever inlines its own literal, a tier added here stops reaching it — the
    subset-registry drift (P11) this shared constant exists to prevent.
    """
    from trw_mcp.channels.meta_tune._throttle import _TIER_LADDER

    assert _TIER_LADDER == list(reversed(TIER_DOWN_LADDER))


@pytest.mark.unit
@pytest.mark.parametrize("name", _REMOVED)
def test_removed_helper_has_no_production_reference(name: str) -> None:
    """A deletion's regression test is the absence of a caller, checked mechanically.

    Scoped to production source: a future re-introduction has to bring a real
    consumer with it rather than arriving as scaffolding again.
    """
    # Word-boundary, not substring: `_throttle` legitimately defines its own
    # `_tier_down` / `_tier_index` with the OPPOSITE unknown-tier fail-safe
    # (unknown -> top, where the removed pair resolved unknown -> floor), and a
    # bare `in` check would flag them. Substring matching over source is the
    # class of bug `.claude/rules/trw-mcp-python.md` calls out for markers.
    pattern = re.compile(rf"(?<!\w){re.escape(name)}\s*\(")
    hits = [
        path.relative_to(_SRC).as_posix()
        for path in _SRC.rglob("*.py")
        if "__pycache__" not in path.parts and pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not hits, f"{name} was removed as an orphan but is referenced again in: {hits}"


@pytest.mark.unit
def test_quota_module_exports_only_the_ladder() -> None:
    """`__all__` is the surface; an export with no implementation is how this drifted."""
    import trw_mcp.channels._quota as quota

    assert quota.__all__ == ["TIER_DOWN_LADDER"]
    for name in _REMOVED:
        assert not hasattr(quota, name), f"{name} is back on the module without a consumer"


@pytest.mark.unit
def test_channels_facade_reexports_match_the_module() -> None:
    """The facade carried the removed names for as long as the module did.

    Parsing ``__all__`` rather than importing keeps this honest about the
    declared surface, which is what a downstream ``from trw_mcp.channels import
    *`` actually gets.
    """
    tree = ast.parse((_SRC / "channels" / "__init__.py").read_text(encoding="utf-8"))
    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            for elt in getattr(node.value, "elts", []):
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    exported.add(elt.value)
    assert "TIER_DOWN_LADDER" in exported
    assert not (exported & set(_REMOVED)), "facade still advertises a removed helper"
