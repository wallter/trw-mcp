"""PRD-INTENT-002 FR01 — PhaseToolPolicy model + inventory-driven coverage.

The policy VALIDATES and NORMALIZES the ``ResolvedProfile.allowed_tools_by_phase``
mapping plus the global Safe Set. It is NOT a parallel hardcoded table — the
default seed (``DEFAULT_PHASE_POLICY``) is the defaults-layer policy that
PROF-001 ships; the middleware consumes the resolved profile, not this constant.

These tests prove:
  - the model round-trips and normalizes phase keys,
  - ``list_for(phase)`` returns phase subset ∪ Safe Set,
  - the rigid tools live in the Safe Set,
  - none of the FIX-076-removed tools appear anywhere,
  - every registered tool (read live from build/inventory.json) is covered.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.models.phase_policy import (
    DEFAULT_PHASE_POLICY,
    RIGID_TOOLS,
    PhaseToolPolicy,
    from_resolved_allowlist,
)

# Tools that PRD-FIX-076 removed; they must never appear in any phase bucket.
_FIX_076_REMOVED = frozenset(
    {
        "trw_ceremony_status",
        "trw_ceremony_approve",
        "trw_ceremony_revert",
        "trw_knowledge_sync",
    }
)


def _inventory_tool_names() -> set[str]:
    """Read the live tool catalogue from build/inventory.json at test time."""
    # tests/ -> trw-mcp/ -> repo root
    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "build" / "inventory.json").read_text())
    return {t["name"] for t in data["tools"]}


def test_policy_round_trip() -> None:
    """FR01: a policy validates and round-trips through model_dump."""
    policy = PhaseToolPolicy(
        allowed_tools_by_phase={"RESEARCH": ["trw_init"], "IMPLEMENT": ["trw_checkpoint"]},
        safe_set=frozenset({"trw_session_start", "trw_status"}),
    )
    rebuilt = PhaseToolPolicy.model_validate(policy.model_dump())
    assert rebuilt.allowed_tools_by_phase == policy.allowed_tools_by_phase
    assert rebuilt.safe_set == policy.safe_set


def test_phase_keys_normalized_to_uppercase() -> None:
    """FR01: lowercase phase keys (the Phase enum value form) are normalized."""
    policy = PhaseToolPolicy(
        allowed_tools_by_phase={"research": ["trw_init"]},
        safe_set=frozenset({"trw_status"}),
    )
    assert "RESEARCH" in policy.allowed_tools_by_phase
    assert "research" not in policy.allowed_tools_by_phase


def test_list_for_returns_phase_subset_union_safe_set() -> None:
    """FR01: list_for(phase) is the phase subset unioned with the Safe Set."""
    policy = PhaseToolPolicy(
        allowed_tools_by_phase={"IMPLEMENT": ["trw_checkpoint"]},
        safe_set=frozenset({"trw_status", "trw_recall"}),
    )
    got = policy.list_for("IMPLEMENT")
    assert got == frozenset({"trw_checkpoint", "trw_status", "trw_recall"})


def test_list_for_accepts_lowercase_phase() -> None:
    """FR01: list_for tolerates the lowercase Phase enum value form."""
    policy = PhaseToolPolicy(
        allowed_tools_by_phase={"VALIDATE": ["trw_build_check"]},
        safe_set=frozenset({"trw_status"}),
    )
    assert policy.list_for("validate") == frozenset({"trw_build_check", "trw_status"})


def test_list_for_unknown_phase_returns_safe_set_only() -> None:
    """FR01: an unknown phase still yields the Safe Set (no crash)."""
    policy = PhaseToolPolicy(
        allowed_tools_by_phase={"RESEARCH": ["trw_init"]},
        safe_set=frozenset({"trw_status"}),
    )
    assert policy.list_for("NONSENSE") == frozenset({"trw_status"})


def test_default_policy_safe_set_contains_rigid_tools() -> None:
    """FR01/invariant: the three rigid tools live in the default Safe Set."""
    for tool in RIGID_TOOLS:
        assert tool in DEFAULT_PHASE_POLICY.safe_set


def test_rigid_tools_visible_in_every_phase() -> None:
    """Invariant: trw_session_start/trw_deliver/trw_build_check in every phase."""
    for phase in ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"):
        visible = DEFAULT_PHASE_POLICY.list_for(phase)
        for tool in RIGID_TOOLS:
            assert tool in visible, f"{tool} missing from {phase}"


def test_default_policy_excludes_fix076_removed_tools() -> None:
    """FR01: none of the FIX-076-removed tools appear in any bucket."""
    all_named = set(DEFAULT_PHASE_POLICY.safe_set)
    for tools in DEFAULT_PHASE_POLICY.allowed_tools_by_phase.values():
        all_named.update(tools)
    assert all_named.isdisjoint(_FIX_076_REMOVED)


def test_all_tool_names_are_registered() -> None:
    """FR01: every registered MCP tool (build/inventory.json) is covered.

    Reading the inventory manifest at test time means a newly-registered tool
    forces a policy update via CI failure (the maintainability gate).
    """
    registered = _inventory_tool_names()
    covered = set(DEFAULT_PHASE_POLICY.safe_set)
    for tools in DEFAULT_PHASE_POLICY.allowed_tools_by_phase.values():
        covered.update(tools)
    missing = registered - covered
    assert not missing, f"tools not in any phase set or Safe Set: {sorted(missing)}"
    # No dangling policy names that aren't real tools.
    dangling = covered - registered
    assert not dangling, f"policy names no real tool: {sorted(dangling)}"


def test_from_resolved_allowlist_uses_profile_surface() -> None:
    """FR01: from_resolved_allowlist builds a policy from a resolved dict.

    This is the SINGLE-SOURCE consumption path: the middleware passes the
    resolved profile's allowed_tools_by_phase and gets a policy back — it never
    defines its own table.
    """
    resolved = {"RESEARCH": ["trw_recall"], "IMPLEMENT": ["trw_learn", "trw_checkpoint"]}
    policy = from_resolved_allowlist(resolved)
    assert policy.allowed_tools_by_phase["IMPLEMENT"] == ["trw_learn", "trw_checkpoint"]
    # Safe Set still carries the rigid tools even when the profile omits them.
    for tool in RIGID_TOOLS:
        assert tool in policy.safe_set


def test_from_resolved_allowlist_none_falls_back_to_default() -> None:
    """FR01: a None/empty resolved allowlist falls back to the default seed."""
    policy = from_resolved_allowlist(None)
    assert policy.allowed_tools_by_phase == DEFAULT_PHASE_POLICY.allowed_tools_by_phase


def test_from_resolved_allowlist_custom_safe_set_still_unions_rigid_tools() -> None:
    """A custom minimal safe_set MUST still get RIGID_TOOLS unioned in.

    Round-2 mutation hardening: the never-hide invariant means
    ``from_resolved_allowlist`` ALWAYS unions RIGID_TOOLS into the effective
    safe set — even when a profile supplies its own narrow ``safe_set`` that
    omits them. The existing coverage only exercised the default-safe-set path
    (which already contains RIGID_TOOLS), so a mutant that dropped the
    ``| RIGID_TOOLS`` union survived: a profile could then hide
    session_start/deliver/build_check and lock a session out of starting,
    validating, or delivering. This test supplies a custom safe_set WITHOUT the
    rigid tools and asserts they are present anyway.
    """
    custom_safe_set = frozenset({"trw_status"})
    assert not (RIGID_TOOLS <= custom_safe_set)  # precondition: the input omits them

    policy = from_resolved_allowlist({"IMPLEMENT": ["trw_checkpoint"]}, safe_set=custom_safe_set)

    for tool in RIGID_TOOLS:
        assert tool in policy.safe_set, f"{tool} must survive a custom safe_set (never-hide invariant)"
    # The supplied non-rigid tool is also preserved.
    assert "trw_status" in policy.safe_set
    # And the rigid tools are therefore visible in every phase.
    for phase in ("RESEARCH", "IMPLEMENT", "DELIVER"):
        for tool in RIGID_TOOLS:
            assert tool in policy.list_for(phase)
