"""Execution, acceptance, and validation models re-exported by requirements."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from trw_mcp.models.requirements import ExecutionState, PRDQualityTier, Priority

# ---------------------------------------------------------------------------
# Activation-gate ownership (PRD-QUAL-119-FR02)
# ---------------------------------------------------------------------------


class ActivationGateOwnership(str, Enum):
    """Who controls an activation gate — determines its completion effect."""

    REPOSITORY_CONTROLLABLE = "repository_controllable"
    EXTERNAL_SYSTEM = "external_system"
    EXTERNAL_RELEASE = "external_release"
    OPERATOR_DECISION = "operator_decision"


class ActivationGate(BaseModel):
    """One typed activation gate on a PRD (PRD-QUAL-119-FR02).

    The ownership class maps DETERMINISTICALLY to a non-completion outcome
    while the gate is open:

    - ``repository_controllable`` open  -> ``incomplete`` (the repo can close it;
      external blockage must never be claimed for work the repo controls);
    - external/operator open WITH verifiable evidence -> ``externally_blocked``;
    - external/operator open WITHOUT evidence -> ``unknown`` (an unverified
      external claim is never a blockage receipt);
    - closed -> ``eligible`` for the remaining completion checks.
    """

    model_config = ConfigDict(strict=True, use_enum_values=True)

    gate_id: str = Field(min_length=1)
    ownership: ActivationGateOwnership
    open: bool = True
    evidence_receipt: str = ""  # verifiable external evidence (receipt id/path)
    detail: str = ""

    def completion_effect(self) -> Literal["incomplete", "externally_blocked", "unknown", "eligible"]:
        if not self.open:
            return "eligible"
        if self.ownership == ActivationGateOwnership.REPOSITORY_CONTROLLABLE.value:
            return "incomplete"
        return "externally_blocked" if self.evidence_receipt.strip() else "unknown"


# ---------------------------------------------------------------------------
# Executable registry — bounded WIP + scheduling ledger (PRD-QUAL-121)
# ---------------------------------------------------------------------------


class PrdActiveLimits(BaseModel):
    """Nested bounded WIP policy (PRD-QUAL-121-FR04). No value can be unbounded —
    every field carries a hard upper cap so an operator override cannot disable
    the limit by setting an effectively-infinite value."""

    model_config = ConfigDict(strict=True)

    global_p0_active_max: int = Field(default=3, ge=1, le=100)
    global_p0_p1_active_max: int = Field(default=12, ge=1, le=500)
    per_owner_p0_active_max: int = Field(default=1, ge=1, le=50)
    per_owner_p0_p1_active_max: int = Field(default=3, ge=1, le=100)
    blocked_external_exception_max: int = Field(default=1, ge=0, le=10)
    candidate_renewal_days: int = Field(default=30, ge=1, le=365)
    queued_renewal_days: int = Field(default=30, ge=1, le=365)
    blocked_external_renewal_days: int = Field(default=7, ge=1, le=90)


class SchedulingAction(BaseModel):
    """Append-only hash-chained scheduling record (PRD-QUAL-121-FR04).

    ``previous_action_digest`` binds each action to the exact ledger head it
    extends; the sole writer rejects forks, gaps, stale heads, rollback, and
    caller-supplied or future dates.
    """

    model_config = ConfigDict(strict=True, use_enum_values=True)

    action_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    kind: Literal["advance_evaluation_epoch", "set_execution_state", "renew"]
    effective_utc_date: str  # ISO YYYY-MM-DD, stamped by the writer's trusted clock
    previous_action_digest: str  # "genesis" for sequence 1
    authorization_receipt: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    payload: dict[str, str] = Field(default_factory=dict)

    @field_validator("effective_utc_date")
    @classmethod
    def _validate_iso_date(cls, v: str) -> str:
        date.fromisoformat(v)
        return v


class EvaluationEpoch(BaseModel):
    """Derived scheduling time: (sequence, effective_utc_date, ledger_head_digest).

    Never caller-supplied; ambient wall-clock changes after ledger commit are
    observation metadata only (PRD-QUAL-121-NFR01).
    """

    model_config = ConfigDict(strict=True)

    sequence: int = Field(ge=0)
    effective_utc_date: str
    ledger_head_digest: str


class RequirementRegistryEntry(BaseModel):
    """One executable active PRD in the generated registry (PRD-QUAL-121-FR03)."""

    model_config = ConfigDict(strict=True, use_enum_values=True, validate_assignment=True)

    prd_id: str
    title: str
    lifecycle_status: str
    priority: str
    category: str
    dependencies: list[str] = Field(default_factory=list)
    owner: str = "unassigned"
    execution_state: ExecutionState = ExecutionState.CANDIDATE
    renewal_date: str = ""  # ISO date of last renewal/state action; "" = never
    source_digest: str = ""  # sha256 of the PRD source bytes


# ---------------------------------------------------------------------------
# AcceptanceManifest + typed reflection lifecycle (PRD-QUAL-120 FR02/FR06)
# ---------------------------------------------------------------------------

ACCEPTANCE_MANIFEST_SCHEMA_VERSION = 1
"""One manifest schema (FR05): every reader and writer uses this version."""


class AcceptedRequirementState(str, Enum):
    """Per-requirement acceptance state derived from scoped evidence."""

    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class AcceptedRequirement(BaseModel):
    """One requirement's acceptance state with its proof or typed blocker."""

    model_config = ConfigDict(strict=True, use_enum_values=True)

    requirement_id: str = Field(min_length=1)
    state: AcceptedRequirementState
    receipt_id: str = ""
    evidence_digest: str = ""  # content digest of the proof artifact
    blocker: str = ""  # mandatory for blocked/unknown


class AcceptanceManifest(BaseModel):
    """Out-of-band derived acceptance truth for one PRD (PRD-QUAL-120-FR02).

    Persists OUTSIDE the PRD source; ``source_digest`` covers the exact raw
    authored PRD bytes. The manifest never writes into those bytes, INDEX, or
    ROADMAP, and its own digest is never an input to itself (no feedback loop).
    """

    model_config = ConfigDict(strict=True, use_enum_values=True)

    schema_version: int = Field(default=ACCEPTANCE_MANIFEST_SCHEMA_VERSION)
    prd_id: str = Field(min_length=1)
    source_digest: str = Field(min_length=8)  # sha256 of raw authored PRD bytes
    derivation_version: str = "acceptance-manifest/v1"
    requirements: list[AcceptedRequirement] = Field(default_factory=list)
    completion_outcome: str = "unknown"

    @field_validator("completion_outcome")
    @classmethod
    def _validate_outcome_vocabulary(cls, value: str) -> str:
        """Audit F8: the outcome must be EffectiveCompletionOutcome vocabulary."""
        from trw_mcp.models.gate_decision import EffectiveCompletionOutcome

        allowed = {outcome.value for outcome in EffectiveCompletionOutcome}
        if value not in allowed:
            raise ValueError(f"completion_outcome must be one of {sorted(allowed)}")
        return value

    def canonical_digest(self) -> str:
        import hashlib
        import json

        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ReflectionActionState(str, Enum):
    """Typed reflection follow-through lifecycle (PRD-QUAL-120-FR06).

    Routing an action to a PRD or backlog is FILING, not closure — only
    VERIFIED_CLOSED (closure evidence against the target) retires debt.
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    ROUTED = "routed"
    IMPLEMENTING = "implementing"
    VERIFIED_CLOSED = "verified_closed"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Wiring gate — seam registry (PRD-CORE-190 FR01)
# ---------------------------------------------------------------------------


class SeamEntry(BaseModel):
    """A declared, not-yet-wired public surface with an owner and expiry.

    Belongs to the requirements.py model module; parsed from PRD frontmatter
    ``seams:`` list by the wiring-gate parser (``_prd_scoring_wiring.py``).
    Backs FR01/FR03/FR04 of PRD-CORE-190.
    """

    model_config = ConfigDict(use_enum_values=True)

    kind: Literal["unimplemented", "unfederated", "deferred", "placeholder"]
    target_prd: str = Field(..., min_length=1)
    owner: str = Field(..., min_length=1)
    expiry_date: str  # ISO-8601 date string (YYYY-MM-DD); validated below
    description: str | None = None

    @field_validator("expiry_date")
    @classmethod
    def _validate_iso_date(cls, v: str) -> str:
        # date.fromisoformat raises ValueError on a malformed date, which
        # Pydantic surfaces as a validation error. The string is retained
        # (not coerced to date) so it round-trips back to YAML byte-identically;
        # FR04's expiry comparison re-parses the string at check time.
        date.fromisoformat(v)
        return v


# ---------------------------------------------------------------------------
# Requirement model
# ---------------------------------------------------------------------------


class Requirement(BaseModel):
    """Individual requirement with confidence and traceability."""

    model_config = ConfigDict(strict=True)

    id: str
    description: str
    priority: Priority = Priority.P1
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    acceptance_criteria: list[str] = Field(default_factory=list)
    traces_to: list[str] = Field(default_factory=list)
    traced_from: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation models
# ---------------------------------------------------------------------------


class ValidationFailure(BaseModel):
    """Individual validation failure from PRD quality check."""

    model_config = ConfigDict(strict=True)

    field: str
    rule: str
    message: str
    severity: str = "error"


class ValidationResult(BaseModel):
    """Result of PRD validation against AARE-F quality gates."""

    model_config = ConfigDict(strict=True)

    valid: bool
    failures: list[ValidationFailure] = Field(default_factory=list)
    ambiguity_rate: float = 0.0
    completeness_score: float = 0.0
    traceability_coverage: float = 0.0
    consistency_score: float = 0.0


class SectionScore(BaseModel):
    """Content density score for a single PRD section (PRD-CORE-008)."""

    model_config = ConfigDict(strict=True)

    section_name: str
    density: float = Field(ge=0.0, le=1.0, default=0.0)
    substantive_lines: int = Field(ge=0, default=0)
    total_lines: int = Field(ge=0, default=0)
    placeholder_lines: int = Field(ge=0, default=0)


class DimensionScore(BaseModel):
    """Score for a single validation dimension (PRD-CORE-008)."""

    model_config = ConfigDict(strict=True)

    name: str
    score: float = Field(ge=0.0, default=0.0)
    max_score: float = Field(ge=0.0, default=1.0)
    details: dict[str, object] = Field(default_factory=dict)


class SmellFinding(BaseModel):
    """Requirement smell detected during validation (PRD-CORE-008)."""

    model_config = ConfigDict(strict=True)

    category: str
    line_number: int = Field(ge=0, default=0)
    matched_text: str = ""
    severity: str = "warning"
    suggestion: str = ""


class ImprovementSuggestion(BaseModel):
    """Actionable suggestion to improve a PRD quality score (PRD-CORE-008)."""

    model_config = ConfigDict(strict=True)

    dimension: str
    priority: str = "medium"
    message: str = ""
    current_score: float = Field(ge=0.0, default=0.0)
    potential_gain: float = Field(ge=0.0, default=0.0)


class ValidationResultV2(BaseModel):
    """Extended validation result with multi-dimension scoring (PRD-CORE-008).

    Includes all fields from ValidationResult for backward compatibility,
    plus multi-dimensional quality scoring, tier classification,
    improvement suggestions, and risk scaling metadata (PRD-QUAL-013).
    """

    model_config = ConfigDict(strict=True)
    _dynamic_base_result: ValidationResultV2 | None = PrivateAttr(default=None)

    # V1 fields (backward compatible)
    valid: bool = True
    failures: list[ValidationFailure] = Field(default_factory=list)
    ambiguity_rate: float = 0.0
    completeness_score: float = 0.0  # deprecated: use total_score (V2) as the authoritative quality metric
    traceability_coverage: float = 0.0
    measured_traceability_coverage: float = (
        0.0  # PRD-QUAL-096: informational ratio (FRs with impl+test refs / total FRs); NOT a gate
    )
    implementation_test_link_coverage: float = 0.0
    verification_mapping_coverage: float = 0.0
    consistency_score: float = 0.0  # reserved — not enforced (consistency scorer not implemented)

    @model_validator(mode="before")
    @classmethod
    def bind_traceability_alias(cls, value: object) -> object:
        """Keep the deprecated implementation-test name an exact alias."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        measured = normalized.get("measured_traceability_coverage", 0.0)
        alias = normalized.get("implementation_test_link_coverage", measured)
        if alias != measured:
            raise ValueError("implementation_test_link_coverage must equal measured_traceability_coverage")
        normalized["implementation_test_link_coverage"] = measured
        return normalized

    # V2 scoring
    total_score: float = Field(ge=0.0, le=100.0, default=0.0)
    quality_tier: PRDQualityTier = PRDQualityTier.SKELETON
    grade: str = "F"
    dimensions: list[DimensionScore] = Field(default_factory=list)
    section_scores: list[SectionScore] = Field(default_factory=list)
    smell_findings: list[SmellFinding] = Field(default_factory=list)
    ears_classifications: list[dict[str, object]] = Field(default_factory=list)
    readability: dict[str, float] = Field(default_factory=dict)
    improvement_suggestions: list[ImprovementSuggestion] = Field(default_factory=list)

    # Risk scaling (PRD-QUAL-013)
    effective_risk_level: str = ""
    risk_scaled: bool = False

    # Status integrity warnings (PRD-FIX-056)
    status_drift_warnings: list[str] = Field(default_factory=list)
    integrity_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------


class TraceabilityResult(BaseModel):
    """Result of traceability analysis."""

    model_config = ConfigDict(strict=True)

    total_requirements: int = 0
    traced_requirements: int = 0
    untraced_requirements: list[str] = Field(default_factory=list)
    coverage: float = 0.0
    orphan_implementations: list[str] = Field(default_factory=list)
    missing_tests: list[str] = Field(default_factory=list)
