"""PRD quality scoring — metric computation for content density, structure, traceability.

Extracted from prd_quality.py to separate scoring (numeric metric computation)
from validation (pass/fail gate checks). All functions here compute and return
DimensionScore / SectionScore values without making pass/fail decisions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    SectionScore,
)
from trw_mcp.state.validation._prd_scoring_ai import (
    _AI_KEYWORD_RE as _AI_KEYWORD_RE,
)
from trw_mcp.state.validation._prd_scoring_ai import (
    _AI_OPERATIONAL_HEADINGS as _AI_OPERATIONAL_HEADINGS,
)
from trw_mcp.state.validation._prd_scoring_ai import (
    _is_ai_agentic_prd as _is_ai_agentic_prd,
)
from trw_mcp.state.validation._prd_scoring_ai import (
    _score_ai_operational_evidence as _score_ai_operational_evidence,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _ASSERTION_BLOCK_RE as _ASSERTION_BLOCK_RE,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _ASSERTION_JSON_TYPE_RE as _ASSERTION_JSON_TYPE_RE,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _ASSERTION_LINE_RE as _ASSERTION_LINE_RE,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _ASSERTION_RE as _ASSERTION_RE,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _ASSERTIONS_HEADING_RE as _ASSERTIONS_HEADING_RE,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _VERIFICATION_COMMAND_RE as _VERIFICATION_COMMAND_RE,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _count_impl_refs as _count_impl_refs,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _count_planned_requirements as _count_planned_requirements,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _count_test_refs as _count_test_refs,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _count_verification_commands as _count_verification_commands,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _has_assertion_evidence as _has_assertion_evidence,
)
from trw_mcp.state.validation._prd_scoring_fr import (
    _FR_HEADING_RE as _FR_HEADING_RE,
)
from trw_mcp.state.validation._prd_scoring_fr import (
    _extract_fr_sections as _extract_fr_sections,
)
from trw_mcp.state.validation._prd_scoring_fr import (
    _score_assertion_coverage as _score_assertion_coverage,
)
from trw_mcp.state.validation._prd_scoring_fr import (
    _score_file_path_coverage as _score_file_path_coverage,
)
from trw_mcp.state.validation._prd_scoring_grounding import (
    compute_grounding_penalty as compute_grounding_penalty,
)
from trw_mcp.state.validation._prd_scoring_grounding import (
    get_project_files as get_project_files,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTION_NAMES,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _HEADING_RE as _HEADING_RE,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _HIGH_WEIGHT_SECTIONS as _HIGH_WEIGHT_SECTIONS,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _PLACEHOLDER_RE as _PLACEHOLDER_RE,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _REQUIRED_SUBSECTIONS_BY_VARIANT as _REQUIRED_SUBSECTIONS_BY_VARIANT,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _REQUIREMENT_LINE_RE as _REQUIREMENT_LINE_RE,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _SECTION_WEIGHTS as _SECTION_WEIGHTS,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _SUBHEADING_RE as _SUBHEADING_RE,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _VAGUE_TERMS_RE as _VAGUE_TERMS_RE,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _compute_ambiguity_rate as _compute_ambiguity_rate,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _extract_subheadings as _extract_subheadings,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _get_section_weights as _get_section_weights,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _is_substantive_line as _is_substantive_line,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _parse_section_content as _parse_section_content,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _validation_profile as _validation_profile,
)
from trw_mcp.state.validation._prd_scoring_readiness import (
    score_implementation_readiness as score_implementation_readiness,
)
from trw_mcp.state.validation._prd_scoring_structural import (
    score_structural_completeness as score_structural_completeness,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _BARE_IMPL_REF_RE as _BARE_IMPL_REF_RE,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _BARE_TEST_REF_RE as _BARE_TEST_REF_RE,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _IMPL_REF_RE as _IMPL_REF_RE,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _KNOWN_TEST_PATTERNS as _KNOWN_TEST_PATTERNS,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _TEST_REF_RE as _TEST_REF_RE,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _collect_reference_matches as _collect_reference_matches,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _count_populated_trace_fields as _count_populated_trace_fields,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _count_table_rows as _count_table_rows,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _extract_fr_id as _extract_fr_id,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _extract_traceability_matrix_rows as _extract_traceability_matrix_rows,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _has_impl_reference as _has_impl_reference,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _has_test_reference as _has_test_reference,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _normalize_reference_token as _normalize_reference_token,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _score_traceability_matrix as _score_traceability_matrix,
)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dimension Scorers
# ---------------------------------------------------------------------------


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
        elif _PLACEHOLDER_RE.match(line) or (line.strip().startswith("<!--") and line.strip().endswith("-->")):
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
    section_weights = _get_section_weights(_config)

    for name, body in sections:
        ss = score_section_density(name, body)
        section_scores.append(ss)
        weight = section_weights.get(name, _config.density_weight_default)
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


def score_traceability_v2(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
    project_root: Path | None = None,
) -> DimensionScore:
    """Score the Traceability dimension (20 points max).

    Checks: traceability link population, traceability matrix row quality.

    Args:
        frontmatter: Parsed YAML frontmatter.
        content: Full PRD markdown content.
        config: Optional config for weight override.
        project_root: Optional absolute path to project root for grounding checks.

    Returns:
        DimensionScore for traceability.
    """
    _config = config or get_config()
    max_score = _config.validation_traceability_weight

    # Check traceability fields in frontmatter
    populated_fields = _count_populated_trace_fields(frontmatter.get("traceability", {}))
    field_ratio = populated_fields / 3
    matrix_score, proof_score = _score_traceability_matrix(content)

    behavior_switch_rows = _count_table_rows(content, "Behavior Switch Matrix")
    behavior_switch_score = min(behavior_switch_rows / max(len(re.findall(r"FR\d+", content)), 1), 1.0)

    # AI/LLM(agentic evaluation, release, monitoring evidence scoring (PRD-QUAL-055)
    ai_operational_evidence_detected = _is_ai_agentic_prd(frontmatter, content)

    ai_evaluation_score = 0.0
    ai_release_score = 0.0
    ai_monitoring_score = 0.0
    if _validation_profile(frontmatter) == "content_docs":
        ai_operational_evidence_detected = False

    if ai_operational_evidence_detected:
        ai_evaluation_score, ai_release_score, ai_monitoring_score = _score_ai_operational_evidence(content)

    ai_operational_evidence_score = (ai_evaluation_score + ai_release_score + ai_monitoring_score) / 3

    # Composite: field population 40%, matrix quality 35%, proof coverage 15%,
    # switch-matrix coverage 10%. AI operational evidence adds 10% weight for AI AGentic PRDs.
    composite = field_ratio * 0.40 + matrix_score * 0.35 + proof_score * 0.15 + behavior_switch_score * 0.10
    if ai_operational_evidence_detected:
        composite = composite * 0.90 + ai_operational_evidence_score * 0.10
    score = composite * max_score

    details: dict[str, object] = {
        "populated_fields": populated_fields,
        "field_ratio": round(field_ratio, 4),
        "matrix_score": round(matrix_score, 4),
        "proof_score": round(proof_score, 4),
        "behavior_switch_score": round(behavior_switch_score, 4),
    }
    profile = _validation_profile(frontmatter)
    if profile:
        details["validation_profile"] = profile
    if ai_operational_evidence_detected:
        details["ai_evaluation_score"] = round(ai_evaluation_score, 4)
        details["ai_release_score"] = round(ai_release_score, 4)
        details["ai_monitoring_score"] = round(ai_monitoring_score, 4)
        details["ai_operational_evidence_score"] = round(ai_operational_evidence_score, 4)
        details["ai_operational_evidence_detected"] = True

    # PRD-QUAL-056-FR01/FR02: File path and assertion coverage sub-dimensions
    fr_sections = _extract_fr_sections(content)
    file_path_cov = _score_file_path_coverage(content, fr_sections)
    assertion_cov = _score_assertion_coverage(content, fr_sections)

    details["file_path_coverage"] = round(file_path_cov, 4)
    details["assertion_coverage"] = round(assertion_cov, 4)

    # Additive bonus: file paths and assertions improve the score but their
    # absence does not penalize (backward compat per NFR01). The 15% ceiling
    # is high enough that partial concrete coverage beats placeholder-only
    # traceability, while still keeping matrix/proof coverage as the primary driver.
    coverage_bonus = (file_path_cov * 0.5 + assertion_cov * 0.5) * 0.15 * max_score
    score = min(score + coverage_bonus, max_score)

    # PRD-QUAL-063: Filesystem Grounding Penalty
    if project_root is not None:
        penalty_mult, hallucinated = compute_grounding_penalty(content, project_root)
        if hallucinated:
            score *= penalty_mult
            details["grounding_penalty_mult"] = round(penalty_mult, 4)
            details["hallucinated_paths"] = len(hallucinated)

    # Suggestions when coverage is low
    suggestions: list[str] = []
    if project_root is not None and hallucinated:
        suggestions.append(
            f"Remove or fix {len(hallucinated)} non-existent file paths (e.g. {hallucinated[0]}) to improve technical grounding."
        )
    if file_path_cov < 0.7:
        suggestions.append(
            "Add implementation and test file paths to FR acceptance criteria for first-pass audit compliance"
        )
    if assertion_cov < 0.5:
        suggestions.append(
            "Add machine-verifiable assertions (grep_present/grep_absent) to FRs for automated audit pre-flight"
        )
    if suggestions:
        details["suggestions"] = suggestions

    return DimensionScore(
        name="traceability",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
    )


