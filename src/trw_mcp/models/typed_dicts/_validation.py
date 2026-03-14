"""Validation dimension / suggestion / failure TypedDicts."""

from __future__ import annotations

from typing import TypedDict


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
    # Ceremony
    ceremony_nudge: dict[str, object]
