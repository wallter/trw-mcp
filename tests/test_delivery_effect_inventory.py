"""PRD-CORE-208 FR03: exhaustive executable delivery-effect inventory."""

from __future__ import annotations

import pytest

from trw_mcp.tools._delivery_effect_registry import (
    DEFERRED_ROSTER_IDS,
    DELIVERY_EFFECT_REGISTRY,
    OperationStateImpact,
    ReplayClass,
    all_effect_ids,
    effects_by_replay_class,
    get_descriptor,
    is_auto_replayable_after_started,
    reconcile_static_roster,
    required_effect_ids,
)

# The approved §6.6 census: S01-S21 plus D00-D24.
_EXPECTED_IDS = frozenset([f"S{n:02d}" for n in range(1, 22)] + [f"D{n:02d}" for n in range(25)])


def test_current_delivery_side_effect_inventory_is_exhaustive() -> None:
    """FR03: registry equals the approved §6.6 census with no gaps or duplicates."""
    assert all_effect_ids() == _EXPECTED_IDS
    assert len(DELIVERY_EFFECT_REGISTRY) == len(_EXPECTED_IDS) == 46
    # Every descriptor's own effect_id matches its dict key (no duplicate/orphan).
    for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
        assert descriptor.effect_id == effect_id
        assert descriptor.owner_call_point  # reachable owner declared


def test_thirteen_deferred_roster_and_post_batch_ids_present() -> None:
    """FR03 acceptance: 13 roster entries + post-batch + D00 lock are represented."""
    # D01-D13 roster, D14-D24 post-batch/nested, D00 coordination lock.
    for n in range(25):
        assert f"D{n:02d}" in DEFERRED_ROSTER_IDS


def test_nonreplayable_effects_never_auto_replay_after_started() -> None:
    """FR04 authority lives in the registry, not code comments."""
    non_replayable = effects_by_replay_class(ReplayClass.NON_REPLAYABLE)
    assert non_replayable  # trust/telemetry/publish/destructive effects exist
    for descriptor in non_replayable:
        assert is_auto_replayable_after_started(descriptor.effect_id) is False
    # Trust increment (D16) and external sends (D07/D14) must be non-replayable.
    for effect_id in ("D16", "D07", "D14", "D01"):
        assert get_descriptor(effect_id).replay_class is ReplayClass.NON_REPLAYABLE
        assert is_auto_replayable_after_started(effect_id) is False


def test_diagnostic_and_coordination_are_not_success_authority() -> None:
    """Gate reads / lock release classified as diagnostic/coordination, reviewable."""
    assert get_descriptor("S21").replay_class is ReplayClass.DIAGNOSTIC
    assert get_descriptor("D00").replay_class is ReplayClass.COORDINATION
    assert get_descriptor("D11").replay_class is ReplayClass.COORDINATION
    # Diagnostic/coordination effects are optional — they never gate success.
    assert get_descriptor("S21").impact is OperationStateImpact.OPTIONAL
    assert get_descriptor("D00").impact is OperationStateImpact.OPTIONAL


def test_required_effects_gate_operation_success() -> None:
    """The required set names the critical mutations that must be proven."""
    required = required_effect_ids()
    for effect_id in ("S01", "S05", "S06", "S18", "S20", "D16"):
        assert effect_id in required


def test_reconcile_flags_orphan_and_missing_mutations() -> None:
    """FR03 census gate: an unregistered write fails; a missing owner fails."""
    clean = reconcile_static_roster(all_effect_ids())
    assert clean == {"missing": (), "orphan": (), "unclassified": ()}

    # A synthetic test-only write with no descriptor is an orphan (fails gate).
    with_orphan = reconcile_static_roster(all_effect_ids() | {"X99_synthetic_write"})
    assert with_orphan["orphan"] == ("X99_synthetic_write",)
    assert with_orphan["unclassified"] == ("X99_synthetic_write",)

    # A registered descriptor whose owner disappeared is missing.
    dropped = reconcile_static_roster(all_effect_ids() - {"D16"})
    assert dropped["missing"] == ("D16",)


def test_get_descriptor_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_descriptor("nope")


def test_registry_descriptors_are_immutable() -> None:
    """Frozen models: a descriptor cannot be mutated after construction (FR03)."""
    descriptor = get_descriptor("S01")
    with pytest.raises(Exception):
        descriptor.replay_class = ReplayClass.KEYED_IDEMPOTENT  # type: ignore[misc]
