"""Validation dimension / suggestion / failure TypedDicts."""

from __future__ import annotations

from typing_extensions import TypedDict


class DimensionScoreDict(TypedDict):
    """One dimension in ``trw_prd_validate`` output."""

    name: str
    score: float
    max_score: float
    details: dict[str, object]


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
    verification: dict[str, object]
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
    measured_traceability_coverage: float
    verification_mapping_coverage: float
    ambiguity_rate: float
    prd_status: str
    sections_found: list[str]
    sections_expected: list[str]
    failures: list[ValidationFailureDict]
    total_score: float
    quality_tier: str
    grade: str
    dimensions: list[DimensionScoreDict]
    improvement_suggestions: list[ImprovementSuggestionDict]
    # PRD token-bloat W5: compact-by-default. ``smell_findings`` is grouped by
    # category (``{category, count, severity, suggestion, sample_lines[:5]}``)
    # in compact mode and a flat per-occurrence list in verbose mode.
    smell_findings: list[dict[str, object]]
    # Compact mode: ``{counts: {pattern: n}, actionable_lines: [int]}``.
    # Verbose mode: full per-line list of ``{line_number, pattern, text}``.
    ears_classifications: list[dict[str, object]] | dict[str, object]
    section_scores: list[SectionScoreDict]
    # True when the response was compacted (verbose=False); False otherwise.
    compact: bool
    effective_risk_level: str
    risk_scaled: bool
    status_drift_warnings: list[str]
    # Integrity warnings surfaced from ``run_prd_integrity_checks``. When the
    # repo bare-filename basename index truncates at a runaway cap
    # (path_index_max_files / path_index_max_seconds), grounding degrades to
    # advisory-skip and a LOUD leading entry prefixed ``path_index_partial:``
    # is inserted here naming how many references were skipped. An agent reading
    # validation output MUST treat that marker as "hallucinated-path detection
    # was NOT performed" rather than "all paths grounded".
    integrity_warnings: list[str]
    # Wiring gate (PRD-CORE-190 FR03): advisory wiring_gate_warning /
    # seam_schema_warning strings for public-surface FRs lacking consumer/
    # wiring_test/seam coverage. Always present (possibly empty); block-mode
    # failures additionally appear in `failures` with rule WIRING_GATE_FAIL.
    wiring_gate_warnings: list[str]
    # PRD-FIX-112: cooperative budget guard + fast mode. ``validation_partial`` is
    # True when fast mode was requested OR the ``prd_validate_budget_seconds``
    # wall-clock budget was exceeded mid-run, in which case ``checks_skipped``
    # names the skipped dynamic check groups (see DYNAMIC_CHECK_GROUPS /
    # INTEGRITY_CHECK_GROUPS) and ``integrity_warnings`` carries a leading
    # ``validation_partial:`` marker. A partial result is NEVER a silent pass —
    # treat it as "the skipped groups were NOT grounded".
    validation_partial: bool
    checks_skipped: list[str]
    # Ceremony
    ceremony_status: str
    # Substrate-First gate (PRD-DIST-218 FR-2)
    substrate_first: dict[str, object]
    cache: dict[str, object]
