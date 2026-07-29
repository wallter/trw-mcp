"""The ONE C1-C9 weaken predicate (PRD-SEC-013 R7). FR01, FR02 and FR08 share it.

Evaluated over the COMPLETE base-to-candidate unique-id claim map BEFORE any
eligibility filtering: filtering first (e.g. to ``binding_channel ==
blocking_hook``) lets a rename, duplicate id, authority downgrade, state exit or
channel downgrade remove a claim from view before it is ever compared.

C8 (precedence / conflicts_with deprioritization) is RESERVED and NOT evaluated
in v0 — the source-schema fields are not part of the loaded binding subset. This
is a documented predicate-coverage gap, not a silent omission.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

from collections.abc import Iterable

from trw_mcp.security.intent_contract._models import (
    BINDING_AUTHORITY,
    Contract,
    MustNotHappenClaim,
    WeakenedClaim,
)

__all__ = [
    "RESERVED_CONDITIONS",
    "evaluate_claim_weakening",
    "evaluate_locator_weakening",
    "is_valid_supersession",
]

#: Conditions defined by the PRD but deliberately not evaluated in v0.
RESERVED_CONDITIONS: tuple[str, ...] = ("C8",)

#: Channels strictly less enforced than ``blocking_hook`` (C7).
_DOWNGRADED_CHANNELS = frozenset({"post_edit_check", "pr_batch", "advisory", "none"})


def _binding(claim: MustNotHappenClaim | None) -> bool:
    return claim is not None and claim.authority_class in BINDING_AUTHORITY


def is_valid_supersession(base: MustNotHappenClaim, candidate: MustNotHappenClaim, snapshot: Contract) -> bool:
    """True iff *candidate* legitimately hands enforcement to a covering claim.

    The successor must itself be ``active``, ``machine_checkable``, ride the
    ``blocking_hook`` channel, and cover a SUPERSET of the original anchors.
    """
    if candidate.state != "superseded" or not candidate.superseded_by:
        return False
    successor = snapshot.by_id().get(candidate.superseded_by)
    if successor is None or successor.claim_id in snapshot.duplicate_ids():
        return False
    return (
        successor.state == "active"
        and successor.machine_checkable
        and successor.binding_channel == "blocking_hook"
        and set(base.anchors) <= set(successor.anchors)
    )


def _field_conditions(base: MustNotHappenClaim, candidate: MustNotHappenClaim) -> list[WeakenedClaim]:
    hits: list[WeakenedClaim] = []
    cid = base.claim_id
    if base.authority_class in BINDING_AUTHORITY and candidate.authority_class not in BINDING_AUTHORITY:
        hits.append(
            WeakenedClaim(
                claim_id=cid,
                condition="C3",
                detail=f"authority_class {base.authority_class} -> {candidate.authority_class}",
            )
        )
    if base.state == "active" and candidate.state != "active":
        hits.append(WeakenedClaim(claim_id=cid, condition="C3", detail=f"state active -> {candidate.state}"))
    if base.falsifiers != candidate.falsifiers:
        kind = "emptied" if candidate.falsifiers == () else "added or changed"
        hits.append(WeakenedClaim(claim_id=cid, condition="C4", detail=f"falsifiers {kind}"))
    if base.machine_checkable and not candidate.machine_checkable:
        hits.append(WeakenedClaim(claim_id=cid, condition="C5", detail="machine_checkable true -> false"))
    removed = tuple(sorted(set(base.anchors) - set(candidate.anchors)))
    if removed:
        hits.append(WeakenedClaim(claim_id=cid, condition="C6", detail=f"anchors removed: {', '.join(removed)}"))
    if base.binding_channel == "blocking_hook" and candidate.binding_channel in _DOWNGRADED_CHANNELS:
        hits.append(
            WeakenedClaim(
                claim_id=cid,
                condition="C7",
                detail=f"binding_channel blocking_hook -> {candidate.binding_channel}",
            )
        )
    return hits


def evaluate_claim_weakening(base: Contract | None, candidate: Contract | None) -> tuple[WeakenedClaim, ...]:
    """Return every C1-C7 predicate hit between two COMPLETE contract snapshots."""
    base_snapshot = base or Contract()
    candidate_snapshot = candidate or Contract()
    base_map = base_snapshot.by_id()
    candidate_map = candidate_snapshot.by_id()
    ambiguous = base_snapshot.duplicate_ids() | candidate_snapshot.duplicate_ids()

    hits: list[WeakenedClaim] = []
    for claim_id in sorted(ambiguous):
        candidates = [c for c in (*base_snapshot.claims, *candidate_snapshot.claims) if c.claim_id == claim_id]
        if any(_binding(c) for c in candidates):
            hits.append(
                WeakenedClaim(
                    claim_id=claim_id,
                    condition="C1",
                    detail="duplicate claim_id — unresolvable by id, treated as weakened",
                )
            )

    for claim_id in sorted(set(base_map) | set(candidate_map)):
        if claim_id in ambiguous:
            continue
        base_claim = base_map.get(claim_id)
        candidate_claim = candidate_map.get(claim_id)
        if base_claim is not None and candidate_claim is None:
            if _binding(base_claim):
                hits.append(WeakenedClaim(claim_id=claim_id, condition="C2", detail="claim removed from candidate"))
            continue
        if base_claim is None and candidate_claim is not None:
            # C4 symmetry (R2): adding a falsifier — including by adding a whole
            # binding claim that carries one — is a protected change.
            if _binding(candidate_claim) and candidate_claim.falsifiers:
                hits.append(
                    WeakenedClaim(claim_id=claim_id, condition="C4", detail="falsifier added on a new binding claim")
                )
            continue
        if base_claim is None or candidate_claim is None:  # pragma: no cover — both-absent is impossible
            continue
        if not (_binding(base_claim) or _binding(candidate_claim)):
            continue
        if is_valid_supersession(base_claim, candidate_claim, candidate_snapshot):
            continue
        hits.extend(_field_conditions(base_claim, candidate_claim))
    return tuple(hits)


def evaluate_locator_weakening(findings: Iterable[str]) -> tuple[WeakenedClaim, ...]:
    """Wrap C9 control-plane/locator findings (computed by ``_control_plane``) as hits."""
    return tuple(WeakenedClaim(claim_id="<control-plane>", condition="C9", detail=finding) for finding in findings)
