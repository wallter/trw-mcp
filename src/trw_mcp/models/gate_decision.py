"""Typed delivery GateDecision IR — PRD-CORE-205 FR07.

Belongs to the models package.

This module defines the authoritative intermediate representation for delivery
gate decisions plus the stable public-result projector.  v26.1 closed the
in-repository receipt migration after review/build producers and readers moved
to enforce-default typed evidence.

The precedence ``NO_ESCAPE -> STRUCTURED -> ADVISORY`` and the public projection
keys mirror :mod:`trw_mcp.tools._deliver_gate_dispatch` exactly — introducing the
IR is a behavior-preserving refactor, sequenced AFTER receipt stabilization so a
regression can be localized to one migration.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.models._evidence_core import SCHEMA_VERSION

# Public compatibility keys the projector must reproduce (frozen legacy surface).
PUBLIC_GATE_KEYS: tuple[str, ...] = (
    "integration_review_block",
    "review_scope_block",
    "review_block",
    "delivery_blocked",
    "build_gate_warning",
    "missing_gate",
    "blocked_task_type",
)


class GateStatus(str, Enum):
    PASS = "pass"  # noqa: S105 — enum member, not a secret
    WARN = "warn"
    BLOCK = "block"
    OVERRIDDEN = "overridden"


class GateOverridePolicy(str, Enum):
    """Mirrors ``_deliver_gate_dispatch.OverridePolicy`` precedence order."""

    NO_ESCAPE = "no_escape"
    STRUCTURED = "structured"
    ADVISORY = "advisory"


# Precedence weight: lower selects first as the controlling decision.
_POLICY_PRECEDENCE: dict[GateOverridePolicy, int] = {
    GateOverridePolicy.NO_ESCAPE: 0,
    GateOverridePolicy.STRUCTURED: 1,
    GateOverridePolicy.ADVISORY: 2,
}


class GateDecision(BaseModel):
    """One evaluated delivery gate as a typed, auditable decision."""

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    decision_id: str
    gate_id: str
    status: GateStatus
    override_policy: GateOverridePolicy
    reason_code: str
    message: str = ""
    task_type: str = "unknown"
    complexity: str = ""
    referenced_receipt_ids: tuple[str, ...] = Field(default_factory=tuple)
    missing_evidence: tuple[str, ...] = Field(default_factory=tuple)
    invalid_evidence: tuple[str, ...] = Field(default_factory=tuple)
    override_record_id: str | None = None
    evaluated_at: str = ""

    @property
    def is_blocking(self) -> bool:
        return self.status is GateStatus.BLOCK


class DeliveryDecisionSet(BaseModel):
    """All co-firing gate decisions; selects the controlling one by precedence."""

    model_config = ConfigDict(strict=True, frozen=True)

    decisions: tuple[GateDecision, ...] = Field(default_factory=tuple)

    def controlling(self) -> GateDecision | None:
        """Return the highest-precedence BLOCKING decision (NO_ESCAPE first).

        A remaining advisory warning never becomes controlling over a block.
        """
        blocking = [d for d in self.decisions if d.is_blocking]
        if not blocking:
            return None
        return min(blocking, key=lambda d: _POLICY_PRECEDENCE[d.override_policy])

    def project_public_keys(self) -> dict[str, str]:
        """Reproduce the existing public gate keys (compatibility projector).

        Maps each blocking/advisory decision to its legacy result key so a caller
        reading the current ``DeliveryGatesDict`` surface sees identical keys.
        """
        projection: dict[str, str] = {}
        for decision in self.decisions:
            if decision.gate_id in PUBLIC_GATE_KEYS and decision.message:
                projection[decision.gate_id] = decision.message
            if decision.gate_id == "delivery_blocked":
                if decision.missing_evidence:
                    projection["missing_gate"] = decision.missing_evidence[0]
                projection["blocked_task_type"] = decision.task_type
        return projection


# ---------------------------------------------------------------------------
# Universal effective completion (PRD-QUAL-119 FR01)
# ---------------------------------------------------------------------------

# NFR03: a decision lists blocking reasons within bounds; excess is truncated
# with an explicit marker rather than scanning/emitting unbounded diagnostics.
MAX_DECISION_REASONS = 20
REASONS_TRUNCATED_MARKER = "reasons_truncated"


class EffectiveCompletionOutcome(str, Enum):
    """The only completion vocabulary any priority may use (PRD-QUAL-119-FR01)."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    EXTERNALLY_BLOCKED = "externally_blocked"
    UNKNOWN = "unknown"
    ROLLED_BACK = "rolled_back"


class CompletionComponentState(str, Enum):
    """Evidence state of one completion component (receipt, wiring, review, …)."""

    CURRENT = "current"
    ABSENT = "absent"
    STALE = "stale"
    INVALID = "invalid"
    CALLER_ASSERTED = "caller_asserted"
    REVOKED = "revoked"


class CompletionComponent(BaseModel):
    """One evidence component consumed by the effective-completion derivation."""

    model_config = ConfigDict(strict=True, frozen=True)

    component_id: str = Field(min_length=1)
    state: CompletionComponentState
    detail: str = ""
    receipt_id: str = ""


class ExternalGateEvidence(BaseModel):
    """An activation gate outside repository control (PRD-QUAL-119-FR02 input).

    ``evidenced=True`` means the blockage carries verifiable external evidence;
    an unverified external claim can only ever yield ``unknown``.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    gate_id: str = Field(min_length=1)
    evidenced: bool
    detail: str = ""


class EffectiveCompletionDecision(BaseModel):
    """Server-derived completion truth for one PRD (PRD-QUAL-119-FR01).

    Deterministic (NFR02): :meth:`canonical_digest` binds every decision input
    surface except ``evaluated_at``, which is observation metadata. Fail-closed
    (NFR01): the deriver can only produce COMPLETE when every component is
    CURRENT and no external gate or rollback applies.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    decision_id: str
    prd_id: str = Field(min_length=1)
    priority: str = ""
    outcome: EffectiveCompletionOutcome
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=MAX_DECISION_REASONS)
    component_states: tuple[CompletionComponent, ...] = Field(default_factory=tuple)
    source_digest: str = ""  # sha256 of the PRD bytes the decision is bound to
    superseded_decision_id: str | None = None  # rollback chain (FR-rollback)
    evaluated_at: str = ""  # observation metadata; excluded from the canonical digest

    def canonical_digest(self) -> str:
        import hashlib
        import json

        payload = self.model_dump(mode="json", exclude={"evaluated_at", "decision_id"})
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @property
    def permits_transition(self) -> bool:
        """Only a CURRENT ``complete`` decision can transition lifecycle (FR01)."""
        return self.outcome is EffectiveCompletionOutcome.COMPLETE


def _bounded_reasons(reasons: list[str]) -> tuple[str, ...]:
    if len(reasons) <= MAX_DECISION_REASONS:
        return tuple(reasons)
    kept = reasons[: MAX_DECISION_REASONS - 1]
    return (*kept, REASONS_TRUNCATED_MARKER)


def derive_effective_completion(
    prd_id: str,
    *,
    priority: str = "",
    components: tuple[CompletionComponent, ...] = (),
    external_gates: tuple[ExternalGateEvidence, ...] = (),
    rolled_back: bool = False,
    rollback_reason: str = "",
    source_digest: str = "",
    decision_id: str = "",
    superseded_decision_id: str | None = None,
    evaluated_at: str = "",
) -> EffectiveCompletionDecision:
    """Derive the single typed completion outcome for any priority (FR01/FR02).

    Fail-closed precedence (highest wins):
      1. ROLLED_BACK — an operator rollback or a REVOKED component supersedes
         every positive projection.
      2. INCOMPLETE — any repository-controllable component ABSENT, INVALID, or
         CALLER_ASSERTED (a caller boolean is never evidence).
      3. UNKNOWN — any STALE component, or an external gate claimed without
         verifiable evidence.
      4. EXTERNALLY_BLOCKED — every repository-controllable component CURRENT
         and at least one EVIDENCED external gate open.
      5. COMPLETE — every component CURRENT, no open gate, no rollback.

    An empty component set is UNKNOWN, never COMPLETE (NFR01: absence of
    evidence is not completion).
    """
    reasons: list[str] = []
    outcome: EffectiveCompletionOutcome

    revoked = [c for c in components if c.state is CompletionComponentState.REVOKED]
    incomplete_states = (
        CompletionComponentState.ABSENT,
        CompletionComponentState.INVALID,
        CompletionComponentState.CALLER_ASSERTED,
    )
    incomplete = [c for c in components if c.state in incomplete_states]
    stale = [c for c in components if c.state is CompletionComponentState.STALE]
    unverified_gates = [g for g in external_gates if not g.evidenced]
    evidenced_gates = [g for g in external_gates if g.evidenced]

    if rolled_back or revoked:
        outcome = EffectiveCompletionOutcome.ROLLED_BACK
        if rollback_reason:
            reasons.append(f"rolled_back: {rollback_reason}")
        reasons.extend(f"revoked: {c.component_id}" for c in revoked)
    elif incomplete:
        outcome = EffectiveCompletionOutcome.INCOMPLETE
        reasons.extend(f"{c.state.value}: {c.component_id}" for c in incomplete)
    elif stale or unverified_gates or not components:
        outcome = EffectiveCompletionOutcome.UNKNOWN
        reasons.extend(f"stale: {c.component_id}" for c in stale)
        reasons.extend(f"unverified_external_gate: {g.gate_id}" for g in unverified_gates)
        if not components:
            reasons.append("no_completion_components_recorded")
    elif evidenced_gates:
        outcome = EffectiveCompletionOutcome.EXTERNALLY_BLOCKED
        reasons.extend(f"externally_blocked: {g.gate_id}" for g in evidenced_gates)
    else:
        outcome = EffectiveCompletionOutcome.COMPLETE

    return EffectiveCompletionDecision(
        decision_id=decision_id,
        prd_id=prd_id,
        priority=priority.upper(),
        outcome=outcome,
        reasons=_bounded_reasons(reasons),
        component_states=components,
        source_digest=source_digest,
        superseded_decision_id=superseded_decision_id,
        evaluated_at=evaluated_at,
    )


COMPATIBILITY_CLOSURE_ID = "core205-v26.1-in-repo-receipt-closure"


def gate_decision_enabled(closure_record_present: bool | None = None) -> bool:
    """Return whether dispatch may use the typed IR.

    The repository default is enabled by the v26.1 closure above.  The optional
    argument remains only as a deterministic test/probe override.
    """
    if closure_record_present is not None:
        return closure_record_present
    return bool(COMPATIBILITY_CLOSURE_ID)
