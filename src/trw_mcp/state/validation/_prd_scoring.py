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
    _AI_OPERATIONAL_HEADINGS as _AI_OPERATIONAL_HEADINGS,
    _is_ai_agentic_prd as _is_ai_agentic_prd,
    _score_ai_operational_evidence as _score_ai_operational_evidence,
)
from trw_mcp.state.validation._prd_scoring_fr import (
    _FR_HEADING_RE as _FR_HEADING_RE,
    _extract_fr_sections as _extract_fr_sections,
    _score_assertion_coverage as _score_assertion_coverage,
    _score_file_path_coverage as _score_file_path_coverage,
)
from trw_mcp.state.validation._prd_scoring_counts import (
    _ASSERTION_BLOCK_RE as _ASSERTION_BLOCK_RE,
    _ASSERTION_JSON_TYPE_RE as _ASSERTION_JSON_TYPE_RE,
    _ASSERTION_LINE_RE as _ASSERTION_LINE_RE,
    _ASSERTION_RE as _ASSERTION_RE,
    _ASSERTIONS_HEADING_RE as _ASSERTIONS_HEADING_RE,
    _VERIFICATION_COMMAND_RE as _VERIFICATION_COMMAND_RE,
    _count_impl_refs as _count_impl_refs,
    _count_planned_requirements as _count_planned_requirements,
    _count_test_refs as _count_test_refs,
    _count_verification_commands as _count_verification_commands,
    _has_assertion_evidence as _has_assertion_evidence,
)
from trw_mcp.state.validation._prd_scoring_grounding import (
    compute_grounding_penalty as compute_grounding_penalty,
    get_project_files as get_project_files,
)
from trw_mcp.state.validation._prd_scoring_parsing import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTION_NAMES,
    _HEADING_RE as _HEADING_RE,
    _HIGH_WEIGHT_SECTIONS as _HIGH_WEIGHT_SECTIONS,
    _PLACEHOLDER_RE as _PLACEHOLDER_RE,
    _REQUIRED_SUBSECTIONS_BY_VARIANT as _REQUIRED_SUBSECTIONS_BY_VARIANT,
    _REQUIREMENT_LINE_RE as _REQUIREMENT_LINE_RE,
    _SECTION_WEIGHTS as _SECTION_WEIGHTS,
    _SUBHEADING_RE as _SUBHEADING_RE,
    _VAGUE_TERMS_RE as _VAGUE_TERMS_RE,
    _compute_ambiguity_rate as _compute_ambiguity_rate,
    _extract_subheadings as _extract_subheadings,
    _get_section_weights as _get_section_weights,
    _is_substantive_line as _is_substantive_line,
    _parse_section_content as _parse_section_content,
    _validation_profile as _validation_profile,
)
from trw_mcp.state.validation._prd_scoring_traceability import (
    _BARE_IMPL_REF_RE as _BARE_IMPL_REF_RE,
    _BARE_TEST_REF_RE as _BARE_TEST_REF_RE,
    _IMPL_REF_RE as _IMPL_REF_RE,
    _KNOWN_TEST_PATTERNS as _KNOWN_TEST_PATTERNS,
    _TEST_REF_RE as _TEST_REF_RE,
    _collect_reference_matches as _collect_reference_matches,
    _count_populated_trace_fields as _count_populated_trace_fields,
    _count_table_rows as _count_table_rows,
    _extract_fr_id as _extract_fr_id,
    _extract_traceability_matrix_rows as _extract_traceability_matrix_rows,
    _has_impl_reference as _has_impl_reference,
    _has_test_reference as _has_test_reference,
    _normalize_reference_token as _normalize_reference_token,
    _score_traceability_matrix as _score_traceability_matrix,
)
from trw_mcp.state.validation.template_variants import get_required_sections, get_variant_for_category

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


def score_structural_completeness(
    frontmatter: dict[str, object],
    sections: list[str],
    config: TRWConfig | None = None,
    category: str | None = None,
    content: str | None = None,
) -> DimensionScore:
    """Score the Structural Completeness dimension (15 points max).

    Checks: category-appropriate sections present, required frontmatter
    fields, confidence scores present (PRD-CORE-080-FR05).

    The expected section count is derived from the PRD's ``category``
    field in frontmatter via the category-to-template-variant mapping.
    Unknown or missing categories default to the 12-section Feature
    template for backward compatibility.

    For AI/LLM/agentic PRDs, also scores AI/agentic operational subsections
    in section 7 ("AI/LLM Operational Sections"): Data/Context Provenance,
    Failure Modes, Human Oversight, Evaluation Plan, Release Gate,
    Monitoring Plan, and Risk Register By Failure Class.

    Args:
        frontmatter: Parsed YAML frontmatter.
        sections: List of section heading names found.
        config: Optional config for weight override.
        category: Optional explicit category override. When ``None``,
            extracted from ``frontmatter["category"]``.
        content: Full PRD markdown content. Required for structural scoring.

    Returns:
        DimensionScore for structural completeness.
    """
    _config = config or get_config()
    max_score = _config.validation_structure_weight

    # Resolve category: explicit param > frontmatter field > default (feature=12)
    resolved_category = category or str(frontmatter.get("category", ""))
    required_sections = get_required_sections(resolved_category)

    # Section coverage: how many of the category-specific expected sections are present
    expected = len(required_sections)
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

    subsection_ratio = 1.0
    matched_subsections = 0
    expected_subsections = 0
    if content is not None:
        from trw_mcp.state.validation.template_variants import get_variant_for_category

        variant = get_variant_for_category(resolved_category)
        required_subsections = _REQUIRED_SUBSECTIONS_BY_VARIANT.get(variant, [])
        expected_subsections = len(required_subsections)
        if required_subsections:
            present_subsections = _extract_subheadings(content)
            matched_subsections = sum(1 for name in required_subsections if name in present_subsections)
            subsection_ratio = matched_subsections / expected_subsections

    # AI/LLM/agentic detection and operational sections scoring (PRD-QUAL-055)
    ai_operational_sections_found = 0
    ai_operational_sections_expected = 7
    ai_section_keywords = [
        "Data / Context Provenance",
        "Failure Modes",
        "Safe Degradation",
        "Human Oversight",
        "Escalation",
        "Evaluation Plan",
        "Release Gate",
        "Monitoring Plan",
        "Risk Register",
        "Failure Class",
    ]

    ai_operational_section_found = False
    if content is not None and _validation_profile(frontmatter) != "content_docs":
        ai_operational_section_found = _is_ai_agentic_prd(frontmatter, content)
        if ai_operational_section_found:
            present_subsections = _extract_subheadings(content)
            ai_operational_sections_found = sum(
                1 for kw in ai_section_keywords if any(kw.lower() in ss.lower() for ss in present_subsections)
            )
            subsection_ratio = (subsection_ratio * 0.75) + (
                ai_operational_sections_found / ai_operational_sections_expected * 0.25
            )

    # Weighted: sections 35%, frontmatter 25%, confidence 15%, required subsections 25%
    composite = section_ratio * 0.35 + fm_ratio * 0.25 + conf_ratio * 0.15 + subsection_ratio * 0.25
    score = composite * max_score

    details: dict[str, object] = {
        "sections_found": found,
        "sections_expected": expected,
        "frontmatter_fields": fm_present,
        "confidence_fields": conf_present,
        "required_subsections_found": matched_subsections,
        "required_subsections_expected": expected_subsections,
    }
    if ai_operational_section_found:
        details["ai_operational_sections_found"] = ai_operational_sections_found
        details["ai_operational_sections_expected"] = ai_operational_sections_expected
        details["ai_section_detected"] = True

    return DimensionScore(
        name="structural_completeness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
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


def score_implementation_readiness(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
    project_root: Path | None = None,
) -> DimensionScore:
    """Score execution-readiness signals distinct from raw prose density.

    Rewards concrete planning evidence such as control points, behavior switches,
    key files, verification tests, and completion/migration semantics. The
    scoring is variant-aware so FIX and RESEARCH PRDs are not penalized for
    missing feature-only scaffolding.
    """
    _config = config or get_config()
    max_score = _config.validation_implementation_readiness_weight
    if not content:
        return DimensionScore(
            name="implementation_readiness",
            score=0.0,
            max_score=max_score,
            details={"variant": "feature"},
        )

    category = str(frontmatter.get("category", ""))
    variant = get_variant_for_category(category)
    fr_sections = _extract_fr_sections(content)
    fr_count = _count_planned_requirements(content, fr_sections)
    impl_refs = _count_impl_refs(content)
    test_refs = _count_test_refs(content)
    verification_commands = _count_verification_commands(content)

    # Pre-compute subheadings once (DRY — avoids redundant regex scans
    # across all variant branches that check for named subheadings).
    present_subheadings = _extract_subheadings(content)

    completion_ratio = (
        sum(
            1
            for heading in (
                "Completion Evidence (Definition of Done)",
                "Migration / Backward Compatibility",
            )
            if heading in present_subheadings
        )
        / 2
    )

    details: dict[str, object] = {
        "variant": variant,
        "fr_count": fr_count,
        "implementation_refs": impl_refs,
        "test_refs": test_refs,
        "verification_commands": verification_commands,
        "completion_ratio": round(completion_ratio, 4),
    }

    profile = _validation_profile(frontmatter)
    if profile:
        details["validation_profile"] = profile

    if profile == "content_docs":
        file_path_ratio = min(impl_refs / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        assertion_ratio = _score_assertion_coverage(content, fr_sections)
        verification_ratio = min(verification_commands / fr_count, 1.0)
        rollout_ratio = (
            1.0 if "Rollout Plan" in content and "Rollback" in content else 0.5 if "Rollout Plan" in content else 0.0
        )
        completion_ratio = (
            1.0 if "Success Metrics" in content and "Traceability Matrix" in content else completion_ratio
        )
        composite = (
            file_path_ratio * 0.30
            + max(test_ref_ratio, assertion_ratio) * 0.25
            + verification_ratio * 0.25
            + rollout_ratio * 0.10
            + completion_ratio * 0.10
        )
        details.update(
            {
                "file_path_ratio": round(file_path_ratio, 4),
                "test_ref_ratio": round(test_ref_ratio, 4),
                "assertion_ratio": round(assertion_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
                "rollout_ratio": round(rollout_ratio, 4),
                "completion_ratio": round(completion_ratio, 4),
            }
        )
    elif variant in {"feature", "infrastructure"}:
        control_point_rows = _count_table_rows(content, "Primary Control Points")
        behavior_switch_rows = _count_table_rows(content, "Behavior Switch Matrix")
        key_files_rows = _count_table_rows(content, "Key Files")
        test_subsections = (
            "Unit Tests",
            "Integration Tests",
            "Acceptance Tests",
            "Regression Tests",
            "Negative / Fallback Tests",
        )
        test_subsection_ratio = sum(1 for heading in test_subsections if heading in present_subheadings) / len(
            test_subsections
        )
        control_ratio = min(control_point_rows / fr_count, 1.0)
        behavior_switch_ratio = min(behavior_switch_rows / fr_count, 1.0)
        file_map_ratio = min(max(key_files_rows, impl_refs) / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        verification_ratio = min(verification_commands / fr_count, 1.0)
        test_plan_ratio = (test_subsection_ratio * 0.5) + (test_ref_ratio * 0.3) + (verification_ratio * 0.2)
        completion_ratio = (completion_ratio * 0.8) + (verification_ratio * 0.2)
        composite = (
            control_ratio * 0.20
            + behavior_switch_ratio * 0.20
            + file_map_ratio * 0.20
            + test_plan_ratio * 0.25
            + completion_ratio * 0.15
        )
        details.update(
            {
                "control_point_rows": control_point_rows,
                "behavior_switch_rows": behavior_switch_rows,
                "key_files_rows": key_files_rows,
                "control_ratio": round(control_ratio, 4),
                "behavior_switch_ratio": round(behavior_switch_ratio, 4),
                "file_map_ratio": round(file_map_ratio, 4),
                "test_subsection_ratio": round(test_subsection_ratio, 4),
                "test_ref_ratio": round(test_ref_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
                "test_plan_ratio": round(test_plan_ratio, 4),
            }
        )
    elif variant == "fix":
        root_cause_ratio = (
            sum(
                1
                for heading in ("Root Cause", "Contributing Factors", "Fix Verification")
                if heading in present_subheadings
            )
            / 3
        )
        regression_ratio = (
            sum(1 for heading in ("Regression Tests", "Negative / Fallback Tests") if heading in present_subheadings)
            / 2
        )
        file_map_ratio = min(max(impl_refs, 1 if "Key Files" in present_subheadings else 0) / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        verification_ratio = min(max(test_ref_ratio, verification_commands / fr_count), 1.0)
        completion_ratio = (completion_ratio * 0.8) + (verification_ratio * 0.2)
        composite = (
            root_cause_ratio * 0.30
            + regression_ratio * 0.20
            + file_map_ratio * 0.20
            + verification_ratio * 0.15
            + completion_ratio * 0.15
        )
        details.update(
            {
                "root_cause_ratio": round(root_cause_ratio, 4),
                "regression_ratio": round(regression_ratio, 4),
                "file_map_ratio": round(file_map_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
            }
        )
    else:
        # Research variant
        present_subheadings_lower = {sub.lower() for sub in present_subheadings}
        research_ratio = (
            sum(
                1
                for heading in ("Approach", "Data Sources", "Evaluation Criteria")
                if any(heading.lower() in sub for sub in present_subheadings_lower)
            )
            / 3
        )
        evidence_ratio = min((impl_refs + test_refs + verification_commands) / 3, 1.0)
        composite = (research_ratio * 0.65) + (evidence_ratio * 0.20) + (completion_ratio * 0.15)
        details.update(
            {
                "research_ratio": round(research_ratio, 4),
                "evidence_ratio": round(evidence_ratio, 4),
            }
        )

    score = composite * max_score

    # PRD-QUAL-063: Filesystem Grounding Penalty
    if project_root is not None:
        penalty_mult, hallucinated = compute_grounding_penalty(content, project_root)
        if hallucinated:
            score *= penalty_mult
            details["grounding_penalty_mult"] = round(penalty_mult, 4)
            details["hallucinated_paths"] = len(hallucinated)

            suggestions: list[str] = details.get("suggestions", [])  # type: ignore
            suggestions.append(
                f"Remove or fix {len(hallucinated)} non-existent file paths (e.g. {hallucinated[0]}) to improve technical grounding."
            )
            details["suggestions"] = suggestions

    return DimensionScore(
        name="implementation_readiness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
    )
