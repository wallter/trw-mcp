"""Typed models for the intent-contract binding subset (PRD-SEC-013).

Only the fields the blocking channels need are modelled (Non-Goals: no full
intent-contract/v0 schema). ``MustNotHappenClaim`` forbids unknown fields so a
contract carrying a field this loader does not understand fails closed rather
than being silently partially enforced.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BINDING_AUTHORITY",
    "ArgvFalsifier",
    "AuthorityClass",
    "BindingChannel",
    "ClaimState",
    "Contract",
    "FalsifierRef",
    "MustNotHappenClaim",
    "PytestFalsifier",
    "SignedCommitViolation",
    "WeakenEditFinding",
    "WeakenedClaim",
]

AuthorityClass = Literal["human_approved", "policy_derived", "mined_provisional", "observational"]
ClaimState = Literal["active", "suspect", "stale", "anchor_lost", "superseded", "expired"]
BindingChannel = Literal["blocking_hook", "post_edit_check", "pr_batch", "advisory", "none"]

#: Authority classes whose weakening requires a verified signature (FR01).
BINDING_AUTHORITY: frozenset[str] = frozenset({"human_approved", "policy_derived"})


class PytestFalsifier(BaseModel):
    """A falsifier expressed as a pytest node id (R2: structured ref, never a shell string)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["pytest"]
    node_id: str = Field(min_length=1)


class ArgvFalsifier(BaseModel):
    """A falsifier expressed as an argv list whose argv[0] must be config-allowlisted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["argv"]
    argv: tuple[str, ...] = Field(min_length=1)


FalsifierRef = Annotated[PytestFalsifier | ArgvFalsifier, Field(discriminator="kind")]


class MustNotHappenClaim(BaseModel):
    """One ``must_not_happen`` claim, binding-subset fields only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    authority_class: AuthorityClass
    state: ClaimState
    machine_checkable: bool
    binding_channel: BindingChannel
    anchors: tuple[str, ...] = ()
    falsifiers: tuple[FalsifierRef, ...] = ()
    superseded_by: str | None = None


class Contract(BaseModel):
    """A parsed contract snapshot. Duplicate ``claim_id`` values are PRESERVED.

    C1 (duplicate-id ambiguity) is only detectable when the loader keeps every
    claim in document order rather than collapsing them into a dict.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str = ""
    claims: tuple[MustNotHappenClaim, ...] = ()

    def duplicate_ids(self) -> frozenset[str]:
        seen: set[str] = set()
        dupes: set[str] = set()
        for claim in self.claims:
            if claim.claim_id in seen:
                dupes.add(claim.claim_id)
            seen.add(claim.claim_id)
        return frozenset(dupes)

    def by_id(self) -> dict[str, MustNotHappenClaim]:
        """Last-wins map. Only meaningful for ids NOT in :meth:`duplicate_ids`."""
        return {claim.claim_id: claim for claim in self.claims}


class WeakenedClaim(BaseModel):
    """One predicate hit: which claim, which C-condition, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    condition: Literal["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C9"]
    detail: str


class SignedCommitViolation(BaseModel):
    """A commit that weakens a binding claim without a verifiable signature."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha: str
    reason: Literal["signature_invalid", "verification_unconfigured", "unresolvable_range"]
    weakened: tuple[WeakenedClaim, ...] = ()
    detail: str = ""


class WeakenEditFinding(BaseModel):
    """A weaken-then-edit pair: the weakening commit plus the protected-path edit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    condition: str
    weakening_sha: str
    editing_sha: str
    edited_paths: tuple[str, ...]
    diff_hash: str
    detail: str = ""
