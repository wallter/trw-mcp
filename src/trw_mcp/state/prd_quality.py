"""PRD quality validation — V1, V2 scoring, and improvement suggestions.

Implements both the simple V1 quality gate (validate_prd_quality) and the
full 6-dimension semantic scorer (validate_prd_quality_v2) including content
density, structural completeness, traceability, and placeholder dimensions.
"""

from __future__ import annotations

import re

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    ImprovementSuggestion,
    PRDQualityGates,
    QualityTier,
    SectionScore,
    SmellFinding,
    ValidationFailure,
    ValidationResult,
    ValidationResultV2,
)
from trw_mcp.state.risk_profiles import derive_risk_level, get_risk_scaled_config

logger = structlog.get_logger()

# Section heading pattern: ## N. Title
_HEADING_RE = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)

# Placeholder patterns for content density (common template defaults)
_PLACEHOLDER_RE = re.compile(
    r"^\s*<!--.*?-->\s*$"
    r"|^\s*\{[^}]+\}\s*$"
    r"|^\s*\[.*TODO.*\]\s*$",
    re.IGNORECASE,
)

# Section headings expected in an AARE-F compliant PRD
_EXPECTED_SECTION_NAMES: list[str] = [
    "Problem Statement",
    "Goals & Non-Goals",
    "User Stories",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Technical Approach",
    "Test Strategy",
    "Rollout Plan",
    "Success Metrics",
    "Dependencies & Risks",
    "Open Questions",
    "Traceability Matrix",
]

# Sections with higher weight in density scoring
_HIGH_WEIGHT_SECTIONS: dict[str, float] = {
    "Problem Statement": 2.0,
    "Functional Requirements": 2.0,
    "Traceability Matrix": 1.5,
}

# Section weights used by external consumers
_SECTION_WEIGHTS: dict[str, float] = _HIGH_WEIGHT_SECTIONS


# ---------------------------------------------------------------------------
# V1 Quality Validation
# ---------------------------------------------------------------------------


def validate_prd_quality(
    frontmatter: dict[str, object],
    sections: list[str],
    gates: PRDQualityGates | None = None,
) -> ValidationResult:
    """Validate a PRD against AARE-F quality gates.

    Args:
        frontmatter: Parsed YAML frontmatter dictionary.
        sections: List of section headings found in the PRD body.
        gates: Quality gate thresholds. Defaults to AARE-F standards.

    Returns:
        ValidationResult with quality scores and any failures.
    """
    _gates = gates or PRDQualityGates()
    failures: list[ValidationFailure] = []

    # Check required frontmatter fields
    required_fields = ["id", "title", "version", "status", "priority"]
    for field in required_fields:
        if field not in frontmatter or not frontmatter[field]:
            failures.append(
                ValidationFailure(
                    field=f"frontmatter:{field}",
                    rule="required_field",
                    message=f"Required frontmatter field missing: {field}",
                    severity="error",
                )
            )

    # Check for 12 required sections
    expected_section_count = 12
    if len(sections) < expected_section_count:
        failures.append(
            ValidationFailure(
                field="sections",
                rule="section_count",
                message=f"PRD has {len(sections)} sections, expected {expected_section_count}",
                severity="error",
            )
        )

    # Check confidence scores exist
    confidence = frontmatter.get("confidence", {})
    if isinstance(confidence, dict):
        confidence_fields = [
            "implementation_feasibility",
            "requirement_clarity",
            "estimate_confidence",
        ]
        for field in confidence_fields:
            if field not in confidence:
                failures.append(
                    ValidationFailure(
                        field=f"confidence:{field}",
                        rule="confidence_present",
                        message=f"Missing confidence score: {field}",
                        severity="warning",
                    )
                )

    # Check traceability
    traceability = frontmatter.get("traceability", {})
    has_traces = False
    if isinstance(traceability, dict):
        for key in ("implements", "depends_on", "enables"):
            val = traceability.get(key, [])
            if isinstance(val, list) and val:
                has_traces = True
                break
    if not has_traces:
        failures.append(
            ValidationFailure(
                field="traceability",
                rule="has_traces",
                message="PRD has no traceability links",
                severity="warning",
            )
        )

    # Calculate scores
    total_checks = len(required_fields) + 3  # sections, confidence, traceability
    error_count = sum(1 for f in failures if f.severity == "error")
    completeness = 1.0 - (error_count / max(total_checks, 1))

    # Traceability coverage: proportion of requirements with traces
    trace_coverage = 1.0 if has_traces else 0.0

    is_valid = (
        completeness >= _gates.completeness_min
        and trace_coverage >= _gates.traceability_coverage_min
        and error_count == 0
    )

    result = ValidationResult(
        valid=is_valid,
        failures=failures,
        completeness_score=completeness,
        traceability_coverage=trace_coverage,
    )

    logger.info(
        "prd_validated",
        valid=is_valid,
        completeness=completeness,
        traceability=trace_coverage,
        failures=len(failures),
    )
    return result


# ---------------------------------------------------------------------------
# V2 Semantic Validation (PRD-CORE-008)
# ---------------------------------------------------------------------------


def _parse_section_content(content: str) -> list[tuple[str, str]]:
    """Split PRD content into (section_name, section_body) pairs.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of (section_name, section_body) tuples.
    """
    # Strip frontmatter
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    fm_match = _FRONTMATTER_RE.match(content)
    body = content[fm_match.end():] if fm_match else content

    sections: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(body))

    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((name, body[start:end]))

    return sections


def _is_substantive_line(line: str) -> bool:
    """Check if a line is substantive (not blank, comment, heading, or placeholder).

    Args:
        line: Single line of text.

    Returns:
        True if the line contains substantive content.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if _PLACEHOLDER_RE.match(line):
        return False
    # Single-line HTML comment
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return False
    # Table separator rows (|---|---|)
    if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
        return False
    # Horizontal rules
    return not re.match(r"^\s*---\s*$", line)


def score_section_density(
    section_name: str,
    section_body: str,
) -> SectionScore:
    """Score the content density of a single PRD section.

    Args:
        section_name: Name of the section.
        section_body: Raw markdown body of the section.

    Returns:
        SectionScore with density ratio and line counts.
    """
    lines = section_body.split("\n")
    total = len(lines)

    substantive = 0
    placeholder = 0
    for line in lines:
        if _is_substantive_line(line):
            substantive += 1
        elif _PLACEHOLDER_RE.match(line) or (
            line.strip().startswith("<!--") and line.strip().endswith("-->")
        ):
            placeholder += 1

    density = substantive / max(total, 1)
    return SectionScore(
        section_name=section_name,
        density=density,
        substantive_lines=substantive,
        total_lines=total,
        placeholder_lines=placeholder,
    )


def score_content_density(
    content: str,
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Content Density dimension (25 points max).

    Computes per-section density and aggregates via weighted average.
    Problem Statement and Functional Requirements get 2x weight;
    Traceability Matrix gets 1.5x weight.

    Args:
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        DimensionScore for content density.
    """
    _config = config or get_config()
    max_score = _config.validation_density_weight

    sections = _parse_section_content(content)
    if not sections:
        return DimensionScore(
            name="content_density",
            score=0.0,
            max_score=max_score,
            details={"section_count": 0},
        )

    section_scores: list[SectionScore] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for name, body in sections:
        ss = score_section_density(name, body)
        section_scores.append(ss)
        weight = _HIGH_WEIGHT_SECTIONS.get(name, 1.0)
        weighted_sum += ss.density * weight
        weight_total += weight

    avg_density = weighted_sum / max(weight_total, 1.0)
    score = avg_density * max_score

    return DimensionScore(
        name="content_density",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "avg_density": round(avg_density, 4),
            "sections_scored": len(section_scores),
        },
    )


def score_structural_completeness(
    frontmatter: dict[str, object],
    sections: list[str],
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Structural Completeness dimension (15 points max).

    Checks: 12 sections present, required frontmatter fields,
    confidence scores present.

    Args:
        frontmatter: Parsed YAML frontmatter.
        sections: List of section heading names found.
        config: Optional config for weight override.

    Returns:
        DimensionScore for structural completeness.
    """
    _config = config or get_config()
    max_score = _config.validation_structure_weight

    # Section coverage: how many of the 12 expected sections are present
    expected = 12
    found = min(len(sections), expected)
    section_ratio = found / expected

    # Frontmatter field coverage
    required_fm_fields = ["id", "title", "version", "status", "priority"]
    fm_present = sum(1 for f in required_fm_fields if frontmatter.get(f))
    fm_ratio = fm_present / len(required_fm_fields)

    # Confidence scores present
    confidence = frontmatter.get("confidence", {})
    confidence_fields = [
        "implementation_feasibility",
        "requirement_clarity",
        "estimate_confidence",
    ]
    conf_present = 0
    if isinstance(confidence, dict):
        conf_present = sum(1 for f in confidence_fields if f in confidence)
    conf_ratio = conf_present / len(confidence_fields)

    # Weighted: sections 50%, frontmatter 30%, confidence 20%
    composite = section_ratio * 0.5 + fm_ratio * 0.3 + conf_ratio * 0.2
    score = composite * max_score

    return DimensionScore(
        name="structural_completeness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "sections_found": found,
            "sections_expected": expected,
            "frontmatter_fields": fm_present,
            "confidence_fields": conf_present,
        },
    )


def score_traceability_v2(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Traceability dimension (20 points max).

    Checks: traceability link population, traceability matrix row quality.

    Args:
        frontmatter: Parsed YAML frontmatter.
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        DimensionScore for traceability.
    """
    _config = config or get_config()
    max_score = _config.validation_traceability_weight

    # Check traceability fields in frontmatter
    trace_data = frontmatter.get("traceability", {})
    trace_fields = ["implements", "depends_on", "enables"]
    populated_fields = 0
    if isinstance(trace_data, dict):
        for field in trace_fields:
            val = trace_data.get(field, [])
            if isinstance(val, list) and val:
                populated_fields += 1

    field_ratio = populated_fields / len(trace_fields)

    # Check traceability matrix content
    matrix_score = 0.0
    if "Traceability Matrix" in content:
        matrix_section = content.split("Traceability Matrix")[-1]
        # Count rows with implementation file references
        impl_refs = re.findall(r"`[\w/]+\.py[:\w]*`", matrix_section)
        test_refs = re.findall(r"`test_[\w]+\.py[:\w]*`", matrix_section)
        # Count FR references in matrix
        fr_refs = re.findall(r"FR\d+", matrix_section)

        ref_count = len(impl_refs) + len(test_refs)
        if fr_refs:
            matrix_score = min(ref_count / max(len(fr_refs), 1), 1.0)

    # Composite: field population 40%, matrix quality 60%
    composite = field_ratio * 0.4 + matrix_score * 0.6
    score = composite * max_score

    return DimensionScore(
        name="traceability",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "populated_fields": populated_fields,
            "field_ratio": round(field_ratio, 4),
            "matrix_score": round(matrix_score, 4),
        },
    )


def classify_quality_tier(
    total_score: float,
    config: TRWConfig | None = None,
) -> QualityTier:
    """Classify a total quality score into a quality tier.

    Args:
        total_score: Score from 0-100.
        config: Optional config for threshold overrides.

    Returns:
        QualityTier enum member.
    """
    _config = config or get_config()
    if total_score >= _config.validation_review_threshold:
        return QualityTier.APPROVED
    if total_score >= _config.validation_draft_threshold:
        return QualityTier.REVIEW
    if total_score >= _config.validation_skeleton_threshold:
        return QualityTier.DRAFT
    return QualityTier.SKELETON


_GRADE_MAP: dict[QualityTier, str] = {
    QualityTier.APPROVED: "A",
    QualityTier.REVIEW: "B",
    QualityTier.DRAFT: "D",
    QualityTier.SKELETON: "F",
}


def map_grade(tier: QualityTier) -> str:
    """Map a quality tier to a letter grade.

    Args:
        tier: Quality tier.

    Returns:
        Letter grade: A, B, D, or F.
    """
    return _GRADE_MAP.get(tier, "F")


def generate_improvement_suggestions(
    dimensions: list[DimensionScore],
    max_suggestions: int = 5,
) -> list[ImprovementSuggestion]:
    """Generate prioritized improvement suggestions for low-scoring dimensions.

    Args:
        dimensions: List of dimension scores.
        max_suggestions: Maximum number of suggestions to return.

    Returns:
        List of suggestions sorted by potential gain descending.
    """
    _messages: dict[str, str] = {
        "content_density": "Add substantive content to sections — replace template placeholders with actual requirements and details.",
        "structural_completeness": "Complete missing sections and frontmatter fields — ensure all 12 AARE-F sections are present.",
        "traceability": "Add traceability links (implements, depends_on, enables) and populate the Traceability Matrix with implementation and test references.",
        "smell_score": "Fix requirement quality issues — remove vague terms, passive voice, and unbounded scope.",
        "readability": "Improve readability — aim for Flesch-Kincaid grade 8-12 for technical documentation.",
        "ears_coverage": "Classify functional requirements using EARS patterns — add trigger keywords (When/While/If/Where) to FR sections.",
    }

    suggestions: list[ImprovementSuggestion] = []
    for dim in dimensions:
        ratio = dim.score / dim.max_score if dim.max_score > 0 else 1.0
        if ratio < 0.7:
            potential_gain = dim.max_score - dim.score
            priority = "high" if ratio < 0.3 else "medium"
            suggestions.append(
                ImprovementSuggestion(
                    dimension=dim.name,
                    priority=priority,
                    message=_messages.get(dim.name, f"Improve {dim.name} score."),
                    current_score=round(dim.score, 2),
                    potential_gain=round(potential_gain, 2),
                )
            )

    suggestions.sort(key=lambda s: s.potential_gain, reverse=True)
    return suggestions[:max_suggestions]


def _coerce_v1_failures(raw: object) -> list[ValidationFailure]:
    """Coerce a V1 failures list from a dict into typed ValidationFailure objects.

    Handles both pre-typed ValidationFailure instances and raw dicts.

    Args:
        raw: Failures value from a v1_result dict (may be list or other).

    Returns:
        List of ValidationFailure instances.
    """
    if not isinstance(raw, list):
        return []
    result: list[ValidationFailure] = []
    for item in raw:
        if isinstance(item, ValidationFailure):
            result.append(item)
        elif isinstance(item, dict):
            result.append(ValidationFailure(
                field=str(item.get("field", "")),
                rule=str(item.get("rule", "")),
                message=str(item.get("message", "")),
                severity=str(item.get("severity", "warning")),
            ))
    return result


def validate_prd_quality_v2(
    content: str,
    config: TRWConfig | None = None,
    v1_result: dict[str, object] | None = None,
    risk_level: str | None = None,
) -> ValidationResultV2:
    """Validate a PRD with full 6-dimension semantic scoring.

    Orchestrates all dimension scorers, computes total score,
    classifies quality tier, and generates improvement suggestions.
    Also populates V1-compatible fields for backward compatibility.

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

    # Score 3 active dimensions (Phase 2a)
    dimensions: list[DimensionScore] = []

    # 1. Content Density (25 pts)
    try:
        density_dim = score_content_density(content, _config)
    except Exception:
        density_dim = DimensionScore(
            name="content_density", score=0.0, max_score=_config.validation_density_weight
        )
    dimensions.append(density_dim)

    # 2. Structural Completeness (15 pts)
    try:
        structure_dim = score_structural_completeness(frontmatter, sections, _config)
    except Exception:
        structure_dim = DimensionScore(
            name="structural_completeness", score=0.0, max_score=_config.validation_structure_weight
        )
    dimensions.append(structure_dim)

    # 3. Traceability (20 pts)
    try:
        trace_dim = score_traceability_v2(frontmatter, content, _config)
    except Exception:
        trace_dim = DimensionScore(
            name="traceability", score=0.0, max_score=_config.validation_traceability_weight
        )
    dimensions.append(trace_dim)

    # 4-6. Placeholder dimensions (modules removed in strip-down; 0-weight)
    smell_findings: list[SmellFinding] = []
    readability_metrics: dict[str, float] = {}
    ears_classifications: list[dict[str, object]] = []
    for placeholder_name in ("smell_score", "readability", "ears_coverage"):
        dimensions.append(DimensionScore(name=placeholder_name, score=0.0, max_score=0.0))

    # Compute total score (normalized to 0-100 against active dimensions)
    max_possible = sum(d.max_score for d in dimensions)
    if max_possible > 0:
        total_score = round(
            min(sum(d.score for d in dimensions) / max_possible * 100.0, 100.0), 2,
        )
    else:
        total_score = 0.0

    # Classify tier and grade
    tier = classify_quality_tier(total_score, _config)
    grade = map_grade(tier)

    # Section scores
    section_scores = [
        score_section_density(name, body)
        for name, body in _parse_section_content(content)
    ]

    # Generate improvement suggestions
    suggestions = generate_improvement_suggestions(dimensions)

    # V1-compatible fields — use pre-computed result if provided (GAP-FR-007)
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

    result = ValidationResultV2(
        # V1 fields (computed inline)
        valid=is_valid,
        failures=v1_failures,
        ambiguity_rate=0.0,
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
