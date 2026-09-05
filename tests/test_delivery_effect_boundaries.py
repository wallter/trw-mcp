"""PRD-FIX-127 FR03/NFR04: one declared crash boundary per registered effect.

Counted from the EXECUTABLE registry, never from prose: before this PRD 24 of the
46 descriptors carried no boundary at all (two of them, ``S02`` and ``S06``, among
the eight marked ``required``), and nothing in the codebase could tell you that
because the boundary was an implicit property of where somebody happened to put a
``journal.step`` call.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.tools._delivery_effect_boundaries import BoundaryKind, EffectBoundary
from trw_mcp.tools._delivery_effect_registry import (
    DELIVERY_EFFECT_REGISTRY,
    UNJOURNALABLE_CLASSES,
    EffectDescriptor,
    OperationStateImpact,
    ReplayClass,
    _validate_boundaries,
    boundary_host,
    unjournaled_effect_ids,
)


def test_every_descriptor_declares_a_crash_boundary() -> None:
    """FR03: zero descriptors carry no boundary, and no shared_with chain is cyclic."""
    undeclared = [d.effect_id for d in DELIVERY_EFFECT_REGISTRY.values() if d.boundary is None]
    assert undeclared == []
    # Floor, not a pin: the 46 approved §6.6 rows plus CORE-244's D25. Later PRDs
    # legitimately add descriptors, and the point of FR03 is that a NEW one cannot
    # arrive without a boundary — the registry raises at import if it tries.
    assert len(DELIVERY_EFFECT_REGISTRY) >= 47

    # Every shared_with resolves, in one hop, to a host that declares `own`.
    for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
        if descriptor.boundary.kind is not BoundaryKind.SHARED_WITH:
            continue
        host = DELIVERY_EFFECT_REGISTRY[descriptor.boundary.host_effect_id]
        assert host.effect_id != effect_id
        assert host.boundary.kind is BoundaryKind.OWN, f"{effect_id} shares fate with a non-own host"

    # boundary_host() therefore terminates for every id (no cycle can exist).
    for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
        host_id = boundary_host(effect_id)
        if descriptor.boundary.kind is BoundaryKind.UNJOURNALED:
            assert host_id == ""
        else:
            assert DELIVERY_EFFECT_REGISTRY[host_id].boundary.kind is BoundaryKind.OWN


def test_no_descriptor_overclaims_its_boundary() -> None:
    """NFR04: unjournaled is diagnostic/coordination only; every host declares own."""
    for effect_id in sorted(unjournaled_effect_ids()):
        descriptor = DELIVERY_EFFECT_REGISTRY[effect_id]
        assert descriptor.replay_class in UNJOURNALABLE_CLASSES, (
            f"{effect_id} claims unjournaled with replay class {descriptor.replay_class.value}"
        )
        assert descriptor.boundary.host_effect_id == ""
    assert sorted(unjournaled_effect_ids()) == ["D00", "D11", "S13", "S21"]


def test_every_required_effect_has_its_own_boundary() -> None:
    """FR03 success metric: required effects with no boundary fall from 2 of 8 to 0."""
    required = [d for d in DELIVERY_EFFECT_REGISTRY.values() if d.impact is OperationStateImpact.REQUIRED]
    assert len(required) == 8
    for descriptor in required:
        assert descriptor.boundary.kind is BoundaryKind.OWN, (
            f"{descriptor.effect_id} is required but its crash outcome is not independently readable"
        )
    # The two the diagnostic named explicitly.
    assert DELIVERY_EFFECT_REGISTRY["S02"].boundary.kind is BoundaryKind.OWN
    assert DELIVERY_EFFECT_REGISTRY["S06"].boundary.kind is BoundaryKind.OWN


def _descriptor(effect_id: str, replay_class: ReplayClass, boundary: EffectBoundary) -> EffectDescriptor:
    return EffectDescriptor(
        effect_id=effect_id,
        mutation="synthetic",
        owner_call_point="synthetic_owner",
        impact=OperationStateImpact.OPTIONAL,
        replay_class=replay_class,
        proof_contract="synthetic",
        boundary=boundary,
    )


def test_registry_rejects_an_unjournaled_non_diagnostic_descriptor() -> None:
    """NFR04: the constraint is enforced at construction, not by review."""
    bad = {
        "X01": _descriptor(
            "X01",
            ReplayClass.POSTCONDITION_PROVABLE,
            EffectBoundary(kind=BoundaryKind.UNJOURNALED, rationale="claims the write proves nothing"),
        )
    }
    with pytest.raises(ValueError, match="requires a diagnostic/coordination"):
        _validate_boundaries(bad)


def test_registry_rejects_a_dangling_or_chained_shared_with() -> None:
    """NFR04: a host must exist and must itself declare ``own``."""
    dangling = {
        "X01": _descriptor(
            "X01",
            ReplayClass.KEYED_IDEMPOTENT,
            EffectBoundary(kind=BoundaryKind.SHARED_WITH, host_effect_id="X99", rationale="dangling"),
        )
    }
    with pytest.raises(ValueError, match="unknown effect"):
        _validate_boundaries(dangling)

    chained = {
        "X01": _descriptor(
            "X01",
            ReplayClass.KEYED_IDEMPOTENT,
            EffectBoundary(kind=BoundaryKind.SHARED_WITH, host_effect_id="X02", rationale="chained"),
        ),
        "X02": _descriptor(
            "X02",
            ReplayClass.KEYED_IDEMPOTENT,
            EffectBoundary(kind=BoundaryKind.SHARED_WITH, host_effect_id="X03", rationale="chained"),
        ),
        "X03": _descriptor("X03", ReplayClass.KEYED_IDEMPOTENT, EffectBoundary(kind=BoundaryKind.OWN, rationale="own")),
    }
    with pytest.raises(ValueError, match="must declare 'own'"):
        _validate_boundaries(chained)

    self_host = {
        "X01": _descriptor(
            "X01",
            ReplayClass.KEYED_IDEMPOTENT,
            EffectBoundary(kind=BoundaryKind.SHARED_WITH, host_effect_id="X01", rationale="self"),
        )
    }
    with pytest.raises(ValueError, match="may not name itself"):
        _validate_boundaries(self_host)


def test_boundary_is_required_and_rationale_is_non_empty() -> None:
    """FR03: the field is required rather than opt-in, so a NEW effect cannot skip it."""
    with pytest.raises(ValidationError):
        EffectDescriptor(  # type: ignore[call-arg]  # justified: proving `boundary` is required
            effect_id="X01",
            mutation="synthetic",
            owner_call_point="synthetic_owner",
            impact=OperationStateImpact.OPTIONAL,
            replay_class=ReplayClass.DIAGNOSTIC,
            proof_contract="synthetic",
        )
    with pytest.raises(ValidationError):
        EffectBoundary(kind=BoundaryKind.OWN, rationale="")
