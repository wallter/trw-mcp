"""Validation dimension / suggestion / failure TypedDicts."""

from __future__ import annotations

from typing_extensions import TypedDict


class DimensionScoreDict(TypedDict):
    """One dimension in ``trw_prd_validate`` output."""

    name: str
    score: float
    max_score: float


class ImprovementSuggestionDict(TypedDict):
    """One improvement suggestion in validation output."""

    dimension: str
    priority: str
    message: str
    current_score: float
    potential_gain: float


class ValidationFailureDict(TypedDict):
    """One validation failure."""

    field: str
    rule: str
    message: str
    severity: str


class SectionScoreDict(TypedDict):
    """Per-section density score."""

    section_name: str
    density: float
    substantive_lines: int


class PrdFrontmatterDict(TypedDict, total=False):
    """Serialized frontmatter dict produced by ``model_to_dict(PRDFrontmatter)``.

    Used as the parameter/return type for ``_strip_deprecated_fields()`` and
    as the ``frontmatter`` parameter in ``_render_prd()``.  All keys are
    optional because ``_strip_deprecated_fields`` removes ``None``-valued and
    deprecated keys before YAML serialization.
    """

    id: str
    title: str
    version: str
    priority: str
    category: str
    risk_level: str | None
    confidence: dict[str, object]
    evidence: dict[str, object]
    traceability: dict[str, object]
    quality_gates: dict[str, object]
    dates: dict[str, object]
    template_version: str | None
    wave_source: str | None
    slos: list[str]
    # Top-level deprecated keys (present pre-strip, absent post-strip)
    aaref_components: object


class PrdCreateResultDict(TypedDict):
    """Return shape of ``trw_prd_create`` MCP tool."""

    prd_id: str
    title: str
    category: str
    priority: str
    output_path: str
    content: str
    sections_generated: int
    index_synced: bool


class ValidateResultDict(TypedDict, total=False):
    """Return shape of ``trw_prd_validate`` MCP tool.

    All keys are present in practice, but total=False allows incremental
    construction in the tool function.
    """

    path: str
    valid: bool
    completeness_score: float
    traceability_coverage: float
    ambiguity_rate: float
    sections_found: list[str]
    sections_expected: list[str]
    failures: list[ValidationFailureDict]
    total_score: float
    quality_tier: str
    grade: str
    dimensions: list[DimensionScoreDict]
    improvement_suggestions: list[ImprovementSuggestionDict]
    smell_findings: list[dict[str, object]]
    ears_classifications: list[dict[str, object]]
    readability: dict[str, float]
    section_scores: list[SectionScoreDict]
    effective_risk_level: str
    risk_scaled: bool
    status_drift_warnings: list[str]
    integrity_warnings: list[str]
    # Ceremony
    ceremony_status: str
