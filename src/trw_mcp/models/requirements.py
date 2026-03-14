"""Requirements models -- PRD, Requirement, Traceability.

AARE-F compliant requirements engineering artifacts.
PRDs follow the 12-section template from AARE-F-FRAMEWORK.md v1.1.0.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class QualityTier(str, Enum):
    """PRD quality tier classification (PRD-CORE-008)."""

    SKELETON = "skeleton"
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"


class PRDStatus(str, Enum):
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


# ---------------------------------------------------------------------------
# PRD component models
# ---------------------------------------------------------------------------


class PRDConfidence(BaseModel):
    """Confidence scores for PRD estimates."""

    model_config = ConfigDict(strict=True)

    implementation_feasibility: float = Field(ge=0.0, le=1.0, default=0.8)
    requirement_clarity: float = Field(ge=0.0, le=1.0, default=0.8)
    estimate_confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    test_coverage_target: float = Field(ge=0.0, le=1.0, default=0.85)


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
    status: PRDStatus = PRDStatus.DRAFT
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
    dates: PRDDates = Field(default_factory=PRDDates)

    # Lifecycle governance (PRD-FIX-056)
    approved_by: str | None = None
    partially_implemented_frs: list[str] = Field(default_factory=list)

    # Optional provenance
    template_version: str | None = None
    wave_source: str | None = None
    slos: list[str] = Field(default_factory=list)


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

    # V1 fields (backward compatible)
    valid: bool = True
    failures: list[ValidationFailure] = Field(default_factory=list)
    ambiguity_rate: float = 0.0
    completeness_score: float = 0.0  # deprecated: use total_score (V2) as the authoritative quality metric
    traceability_coverage: float = 0.0
    consistency_score: float = 0.0  # reserved — not enforced (consistency scorer not implemented)

    # V2 scoring
    total_score: float = Field(ge=0.0, le=100.0, default=0.0)
    quality_tier: QualityTier = QualityTier.SKELETON
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
