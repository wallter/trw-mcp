"""PRD quality validation — orchestrator and public API.

Thin facade that re-exports scoring functions (from ``_prd_scoring``)
and validation functions (from ``_prd_validation``), plus the
``validate_prd_quality_v2()`` orchestrator that ties both concerns together.

All previously public names are re-exported so that existing imports
(``from trw_mcp.state.validation.prd_quality import ...``) continue to
work without modification.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    PRDQualityGates,
    SmellFinding,
    ValidationResultV2,
)
from trw_mcp.state.validation.risk_profiles import derive_risk_level, get_risk_scaled_config

# ---------------------------------------------------------------------------
# Re-exports from _prd_scoring (metric computation)
# ---------------------------------------------------------------------------
from trw_mcp.state.validation._prd_scoring import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTION_NAMES,
)
from trw_mcp.state.validation._prd_scoring import (
    _HEADING_RE as _HEADING_RE,
)
from trw_mcp.state.validation._prd_scoring import (
    _HIGH_WEIGHT_SECTIONS as _HIGH_WEIGHT_SECTIONS,
)
from trw_mcp.state.validation._prd_scoring import (
    _KNOWN_TEST_PATTERNS as _KNOWN_TEST_PATTERNS,
)
from trw_mcp.state.validation._prd_scoring import (
    _PLACEHOLDER_RE as _PLACEHOLDER_RE,
)
from trw_mcp.state.validation._prd_scoring import (
    _SECTION_WEIGHTS as _SECTION_WEIGHTS,
)
from trw_mcp.state.validation._prd_scoring import (
    _TEST_REF_RE as _TEST_REF_RE,
)
from trw_mcp.state.validation._prd_scoring import (
    _compute_ambiguity_rate as _compute_ambiguity_rate,
)
from trw_mcp.state.validation._prd_scoring import (
    _is_substantive_line as _is_substantive_line,
)
from trw_mcp.state.validation._prd_scoring import (
    _parse_section_content as _parse_section_content,
)
from trw_mcp.state.validation._prd_scoring import (
    score_content_density as score_content_density,
)
from trw_mcp.state.validation._prd_scoring import (
    score_section_density as score_section_density,
)
from trw_mcp.state.validation._prd_scoring import (
    score_structural_completeness as score_structural_completeness,
)
from trw_mcp.state.validation._prd_scoring import (
    score_traceability_v2 as score_traceability_v2,
)

# ---------------------------------------------------------------------------
# Re-exports from _prd_validation (gate checks, tier classification)
# ---------------------------------------------------------------------------
from trw_mcp.state.validation._prd_validation import (
    _GRADE_MAP as _GRADE_MAP,
)
from trw_mcp.state.validation._prd_validation import (
    _check_fr_annotations as _check_fr_annotations,
)
from trw_mcp.state.validation._prd_validation import (
    _check_partially_implemented as _check_partially_implemented,
)
from trw_mcp.state.validation._prd_validation import (
    _check_sprint_deferral as _check_sprint_deferral,
)
from trw_mcp.state.validation._prd_validation import (
    _check_status_drift as _check_status_drift,
)
from trw_mcp.state.validation._prd_validation import (
    _coerce_v1_failures as _coerce_v1_failures,
)
from trw_mcp.state.validation._prd_validation import (
    classify_quality_tier as classify_quality_tier,
)
from trw_mcp.state.validation._prd_validation import (
    generate_improvement_suggestions as generate_improvement_suggestions,
)
from trw_mcp.state.validation._prd_validation import (
    map_grade as map_grade,
)
from trw_mcp.state.validation._prd_validation import (
    validate_prd_quality as validate_prd_quality,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# V2 Orchestrator (PRD-CORE-008)
# ---------------------------------------------------------------------------


def validate_prd_quality_v2(
    content: str,
    config: TRWConfig | None = None,
    v1_result: dict[str, object] | None = None,
    risk_level: str | None = None,
    *,
    project_root: str | None = None,
) -> ValidationResultV2:
    """Validate a PRD with 3-dimension semantic scoring.

    Orchestrates all active dimension scorers (content_density,
    structural_completeness, traceability), computes total score,
    classifies quality tier, and generates improvement suggestions.
    Also populates V1-compatible fields for backward compatibility.
    Stub dimensions (smell_score, readability, ears_coverage) are reserved
    for future implementation and are NOT included in dimensions output.

    When risk_level is provided (or derived from frontmatter priority),
    thresholds and dimension weights are adjusted per RISK_PROFILES
    (PRD-QUAL-013).

    Args:
        content: Full PRD markdown content.
        config: Optional TRWConfig for threshold/weight overrides.
        v1_result: Optional pre-computed V1 validation result. When provided,
            V1 fields are populated from this dict, skipping redundant
            V1 computation (GAP-FR-007 optimization).
        risk_level: Optional explicit risk level override. If None,
            derived from frontmatter priority field.
        project_root: Optional project root path for sprint deferral detection
            (R-03). When provided, sprint docs are scanned for deferral language
            near the PRD ID.

    Returns:
        ValidationResultV2 with all dimension scores and metadata.
    """
    _config = config or get_config()

    # Parse frontmatter and sections using shared utils
    from trw_mcp.state.prd_utils import extract_sections, parse_frontmatter

    frontmatter = parse_frontmatter(content)
    sections = extract_sections(content)

    # PRD-QUAL-013: Derive risk level and apply scaling
    fm_priority = str(frontmatter.get("priority", "P2"))
    fm_risk = frontmatter.get("risk_level")
    explicit_risk = risk_level or (str(fm_risk) if fm_risk else None)
    effective_risk = derive_risk_level(fm_priority, explicit_risk)
    _config = get_risk_scaled_config(_config, effective_risk)
    is_risk_scaled = effective_risk != "medium" and _config.risk_scaling_enabled

    # Score 3 active dimensions -- content density, structural completeness, traceability.
    # Stub dimensions (smell_score, readability, ears_coverage) are reserved for future
    # implementation and are NOT appended here (FR01 -- PRD-FIX-054).
    _active_dims: list[tuple[str, Callable[[], DimensionScore], float]] = [
        ("content_density", lambda: score_content_density(content, _config), _config.validation_density_weight),
        (
            "structural_completeness",
            lambda: score_structural_completeness(frontmatter, sections, _config, str(frontmatter.get("category", ""))),
            _config.validation_structure_weight,
        ),
        (
            "traceability",
            lambda: score_traceability_v2(frontmatter, content, _config),
            _config.validation_traceability_weight,
        ),
    ]
    dimensions: list[DimensionScore] = []
    for dim_name, scorer, max_score in _active_dims:
        try:
            dimensions.append(scorer())
        except Exception:  # per-item error handling: one dimension failure must not block entire scoring  # noqa: PERF203
            logger.warning("dimension_scoring_failed", dimension=dim_name, exc_info=True)
            dimensions.append(DimensionScore(name=dim_name, score=0.0, max_score=max_score))

    # Backward-compatible placeholder collections -- remain empty (no scorer behind them)
    smell_findings: list[SmellFinding] = []
    readability_metrics: dict[str, float] = {}
    ears_classifications: list[dict[str, object]] = []

    # Compute total score (normalized to 0-100 against active dimensions)
    max_possible = sum(d.max_score for d in dimensions)
    if max_possible > 0:
        total_score = round(
            min(sum(d.score for d in dimensions) / max_possible * 100.0, 100.0),
            2,
        )
    else:
        total_score = 0.0

    # Classify tier and grade
    tier = classify_quality_tier(total_score, _config)
    grade = map_grade(tier)

    # Section scores
    section_scores = [score_section_density(name, body) for name, body in _parse_section_content(content)]

    # Generate improvement suggestions
    suggestions = generate_improvement_suggestions(dimensions)

    # V1-compatible fields -- use pre-computed result if provided (GAP-FR-007)
    if v1_result is not None:
        v1_failures = _coerce_v1_failures(v1_result.get("failures", []))
        is_valid = bool(v1_result.get("valid", False))
        v1_completeness = float(str(v1_result.get("completeness_score", 0.0)))
        v1_trace_coverage = float(str(v1_result.get("traceability_coverage", 0.0)))
    else:
        # Delegate to V1 with risk-scaled gates (PRD-FIX-011)
        v1_gates = PRDQualityGates(
            completeness_min=_config.completeness_min,
            traceability_coverage_min=_config.traceability_coverage_min,
        )
        v1 = validate_prd_quality(frontmatter, sections, v1_gates)
        v1_failures = v1.failures
        is_valid = v1.valid
        v1_completeness = v1.completeness_score
        v1_trace_coverage = v1.traceability_coverage

    # Compute ambiguity rate from content (FR02 -- PRD-FIX-054)
    ambiguity_rate = _compute_ambiguity_rate(content)

    # PRD-FIX-056: Status integrity checks (informational -- never block scoring)
    status_drift_warnings: list[str] = []
    try:
        status_drift_warnings.extend(_check_status_drift(frontmatter, content))
        status_drift_warnings.extend(_check_fr_annotations(content))
        status_drift_warnings.extend(_check_partially_implemented(frontmatter))
        # R-03: Sprint doc deferral detection
        if project_root is not None:
            from pathlib import Path as _Path

            status_drift_warnings.extend(
                _check_sprint_deferral(frontmatter, project_root=_Path(project_root))
            )
    except Exception:  # justified: fail-open, integrity checks must not block scoring
        logger.warning("status_integrity_check_failed", exc_info=True)

    result = ValidationResultV2(
        # V1 fields (computed inline)
        valid=is_valid,
        failures=v1_failures,
        ambiguity_rate=ambiguity_rate,
        completeness_score=v1_completeness,
        traceability_coverage=v1_trace_coverage,
        consistency_score=0.0,
        # V2 fields
        total_score=total_score,
        quality_tier=tier,
        grade=grade,
        dimensions=dimensions,
        section_scores=section_scores,
        smell_findings=smell_findings,
        ears_classifications=ears_classifications,
        readability=readability_metrics,
        improvement_suggestions=suggestions,
        # Risk scaling fields (PRD-QUAL-013)
        effective_risk_level=effective_risk,
        risk_scaled=is_risk_scaled,
        # Status integrity warnings (PRD-FIX-056)
        status_drift_warnings=status_drift_warnings,
    )

    logger.info(
        "prd_validated_v2",
        total_score=total_score,
        quality_tier=tier.value,
        grade=grade,
        dimensions_scored=len(dimensions),
        effective_risk_level=effective_risk,
        risk_scaled=is_risk_scaled,
    )
    return result
