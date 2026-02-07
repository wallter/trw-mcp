"""Requirements models — PRD, Requirement, Traceability.

These models represent AARE-F compliant requirements engineering artifacts.
PRDs follow the 12-section template from AARE-F-FRAMEWORK.md v1.1.0.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PRDStatus(str, Enum):
    """PRD lifecycle status."""

    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    DEPRECATED = "deprecated"


class Priority(str, Enum):
    """Requirement priority levels."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class EvidenceLevel(str, Enum):
    """Evidence strength classification (AARE-F C6)."""

    STRONG = "strong"
    MODERATE = "moderate"
    LIMITED = "limited"
    THEORETICAL = "theoretical"


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


class PRDFrontmatter(BaseModel):
    """YAML frontmatter for an AARE-F compliant PRD.

    Contains all metadata fields from the AARE-F PRD template.
    The 12 content sections are stored as markdown body text.
    """

    model_config = ConfigDict(strict=True)

    id: str
    title: str
    version: str = "1.0"
    status: PRDStatus = PRDStatus.DRAFT
    priority: Priority = Priority.P1
    category: str = ""
    aaref_components: list[str] = Field(default_factory=list)
    evidence: PRDEvidence = Field(default_factory=PRDEvidence)
    confidence: PRDConfidence = Field(default_factory=PRDConfidence)
    traceability: PRDTraceability = Field(default_factory=PRDTraceability)
    metrics: PRDMetrics = Field(default_factory=PRDMetrics)
    quality_gates: PRDQualityGates = Field(default_factory=PRDQualityGates)
    dates: PRDDates = Field(default_factory=PRDDates)


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


class TraceabilityResult(BaseModel):
    """Result of traceability analysis."""

    model_config = ConfigDict(strict=True)

    total_requirements: int = 0
    traced_requirements: int = 0
    untraced_requirements: list[str] = Field(default_factory=list)
    coverage: float = 0.0
    orphan_implementations: list[str] = Field(default_factory=list)
    missing_tests: list[str] = Field(default_factory=list)
