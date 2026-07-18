"""Requirements models -- PRD, Requirement, Traceability.

AARE-F compliant requirements engineering artifacts.
PRDs follow the category-variant template from AARE-F-FRAMEWORK.md
(feature=12 sections, infrastructure=9, fix=8, research=7).
"""

# ruff: noqa: E402, I001

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PRDQualityTier(str, Enum):
    """PRD document-quality classification, distinct from lifecycle status."""

    SKELETON = "skeleton"
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"


class PRDLifecycleStatus(str, Enum):
    """PRD lifecycle status.

    Lifecycle: draft -> review -> approved -> implemented -> done
    Terminal states: deprecated (any state), merged (any state), done
    """

    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    DONE = "done"
    MERGED = "merged"
    DEPRECATED = "deprecated"


# PRD status state machine (PRD-CORE-009-FR01, PRD-FIX-008)
# Identity transitions (same → same) are always valid and handled in is_valid_transition.
# Terminal states: done, merged, deprecated — no outgoing transitions.
VALID_TRANSITIONS: dict[PRDLifecycleStatus, set[PRDLifecycleStatus]] = {
    PRDLifecycleStatus.DRAFT: {PRDLifecycleStatus.REVIEW, PRDLifecycleStatus.MERGED},
    PRDLifecycleStatus.REVIEW: {
        PRDLifecycleStatus.APPROVED,
        PRDLifecycleStatus.DRAFT,
        PRDLifecycleStatus.MERGED,
    },
    PRDLifecycleStatus.APPROVED: {
        PRDLifecycleStatus.IMPLEMENTED,
        PRDLifecycleStatus.DEPRECATED,
        PRDLifecycleStatus.MERGED,
    },
    PRDLifecycleStatus.IMPLEMENTED: {PRDLifecycleStatus.DONE, PRDLifecycleStatus.DEPRECATED},
    PRDLifecycleStatus.DONE: set(),
    PRDLifecycleStatus.MERGED: set(),
    PRDLifecycleStatus.DEPRECATED: set(),
}

# Backward-compatible import names. New code should use the explicit names so
# lifecycle state cannot be confused with a document-quality tier that happens
# to use some of the same wire values.
QualityTier = PRDQualityTier
PRDStatus = PRDLifecycleStatus


class Priority(str, Enum):
    """Requirement priority levels."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RiskLevel(str, Enum):
    """Risk classification for AARE-F C3 Risk-Based Rigor."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ComplexityFactor(str, Enum):
    """Complexity classification for AARE-F C3 effort estimation."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class EvidenceLevel(str, Enum):
    """Evidence strength classification (AARE-F C6)."""

    STRONG = "strong"
    MODERATE = "moderate"
    LIMITED = "limited"
    THEORETICAL = "theoretical"


class VerificationMethod(str, Enum):
    """AARE-F 3.2 requirement-verification methods."""

    TEST = "test"
    ANALYSIS = "analysis"
    INSPECTION = "inspection"
    DEMONSTRATION = "demonstration"


class ExecutionState(str, Enum):
    """Executable-registry queue state, orthogonal to lifecycle status (PRD-QUAL-121-FR04).

    Only ACTIVE and BLOCKED_EXTERNAL consume work-in-progress slots.
    """

    CANDIDATE = "candidate"
    QUEUED = "queued"
    ACTIVE = "active"
    BLOCKED_EXTERNAL = "blocked_external"
    CLOSING = "closing"


# ---------------------------------------------------------------------------
# PRD component models
# ---------------------------------------------------------------------------


class PRDConfidence(BaseModel):
    """Confidence scores for PRD estimates."""

    model_config = ConfigDict(strict=True)

    implementation_feasibility: float = Field(ge=0.0, le=1.0, default=0.8)
    requirement_clarity: float = Field(ge=0.0, le=1.0, default=0.8)
    estimate_confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    test_coverage_target: float | None = Field(ge=0.0, le=1.0, default=None)


class PRDEvidence(BaseModel):
    """Evidence metadata for a PRD."""

    model_config = ConfigDict(strict=True)

    level: EvidenceLevel = EvidenceLevel.MODERATE
    sources: list[str] = Field(default_factory=list)


class PRDTraceability(BaseModel):
    """Traceability links for a PRD."""

    model_config = ConfigDict(strict=True)

    implements: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    enables: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)


class PRDMetrics(BaseModel):
    """Success metrics for a PRD."""

    model_config = ConfigDict(strict=True)

    success_criteria: list[str] = Field(default_factory=list)
    measurement_method: list[str] = Field(default_factory=list)


class PRDQualityGates(BaseModel):
    """Quality gate thresholds for a PRD (from AARE-F)."""

    model_config = ConfigDict(strict=True)

    ambiguity_rate_max: float = Field(ge=0.0, le=1.0, default=0.05)
    completeness_min: float = Field(ge=0.0, le=1.0, default=0.85)
    traceability_coverage_min: float = Field(ge=0.0, le=1.0, default=0.90)
    consistency_validation_min: float = Field(ge=0.0, le=1.0, default=0.95)


class VerificationMapping(BaseModel):
    """Typed requirement-to-verification contract from AARE-F 3.2 §2.5."""

    model_config = ConfigDict(strict=True, use_enum_values=True)

    requirement_id: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list, min_length=1)
    method: VerificationMethod
    evidence_artifact: str = Field(min_length=1)
    pass_condition: str = Field(min_length=1)
    automated: bool | None = None
    automation_infeasible_reason: str | None = None

    @field_validator("requirement_id", "evidence_artifact", "pass_condition", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        """Strip required mapping scalars and reject whitespace-only evidence."""
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("verification mapping text must not be blank")
            return normalized
        return value

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def normalize_acceptance_criteria(cls, value: object) -> object:
        """Normalize every criterion; hollow list items are never coverage."""
        if not isinstance(value, list):
            return value
        normalized: list[object] = []
        for item in value:
            if isinstance(item, str):
                item = item.strip()
                if not item:
                    raise ValueError("acceptance criteria must not contain blank items")
            normalized.append(item)
        return normalized

    @field_validator("automation_infeasible_reason", mode="before")
    @classmethod
    def normalize_optional_reason(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("automation infeasible reason must not be blank when supplied")
            return normalized
        return value


class PRDVerification(BaseModel):
    """Collection of typed verification mappings for a PRD."""

    model_config = ConfigDict(strict=True)

    mappings: list[VerificationMapping] = Field(default_factory=list)


class PRDDates(BaseModel):
    """Date tracking for a PRD."""

    model_config = ConfigDict(strict=True)

    created: date = Field(default_factory=date.today)
    updated: date = Field(default_factory=date.today)
    target_completion: date | None = None


# ---------------------------------------------------------------------------
# PRD frontmatter (aggregate model)
# ---------------------------------------------------------------------------


class PRDFrontmatter(BaseModel):
    """YAML frontmatter for an AARE-F compliant PRD.

    Contains all metadata fields from the AARE-F PRD template.
    The 12 content sections are stored as markdown body text.
    """

    model_config = ConfigDict(strict=True, use_enum_values=True)

    # Identity
    id: str
    title: str
    version: str = "1.0"

    # Classification
    status: PRDLifecycleStatus = PRDLifecycleStatus.DRAFT
    priority: Priority = Priority.P1
    category: str = ""
    risk_level: RiskLevel | None = None
    complexity: ComplexityFactor | None = None

    # AARE-F nested metadata
    # Deprecated: aaref_components — never validated or consumed by any tool (PRD-CORE-080-FR07).
    # Present for backward compat with existing PRDs; omitted from new PRD generation.
    aaref_components: list[str] | None = Field(default=None)
    evidence: PRDEvidence = Field(default_factory=PRDEvidence)
    confidence: PRDConfidence = Field(default_factory=PRDConfidence)
    traceability: PRDTraceability = Field(default_factory=PRDTraceability)
    metrics: PRDMetrics = Field(default_factory=PRDMetrics)
    quality_gates: PRDQualityGates = Field(default_factory=PRDQualityGates)
    verification: PRDVerification = Field(default_factory=PRDVerification)
    dates: PRDDates = Field(default_factory=PRDDates)

    # Lifecycle governance (PRD-FIX-056)
    approved_by: str | None = None
    partially_implemented_frs: list[str] = Field(default_factory=list)

    # Typed activation gates (PRD-QUAL-119-FR02): open gates map deterministically
    # to non-completion outcomes; see ActivationGate.completion_effect.
    activation_gates: list[ActivationGate] = Field(default_factory=list)

    # Optional provenance
    template_version: str | None = None
    wave_source: str | None = None
    slos: list[str] = Field(default_factory=list)


from trw_mcp.models._requirements_execution import (
    ACCEPTANCE_MANIFEST_SCHEMA_VERSION as ACCEPTANCE_MANIFEST_SCHEMA_VERSION,
    ActivationGateOwnership as ActivationGateOwnership,
    ActivationGate as ActivationGate,
    PrdActiveLimits as PrdActiveLimits,
    SchedulingAction as SchedulingAction,
    EvaluationEpoch as EvaluationEpoch,
    RequirementRegistryEntry as RequirementRegistryEntry,
    AcceptedRequirementState as AcceptedRequirementState,
    AcceptedRequirement as AcceptedRequirement,
    AcceptanceManifest as AcceptanceManifest,
    ReflectionActionState as ReflectionActionState,
    SeamEntry as SeamEntry,
    Requirement as Requirement,
    ValidationFailure as ValidationFailure,
    ValidationResult as ValidationResult,
    SectionScore as SectionScore,
    DimensionScore as DimensionScore,
    SmellFinding as SmellFinding,
    ImprovementSuggestion as ImprovementSuggestion,
    ValidationResultV2 as ValidationResultV2,
    TraceabilityResult as TraceabilityResult,
)
