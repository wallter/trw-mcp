"""PRD scoring — structural-completeness dimension scorer.

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

Scores the Structural Completeness dimension (15 points max) covering
section coverage, frontmatter field coverage, confidence-score presence,
template-variant required subsections, and AI/LLM/agentic operational
subsections (PRD-QUAL-055).

Extracted as DIST-243 batch 62.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import DimensionScore
from trw_mcp.state.validation._prd_scoring_ai import _is_ai_agentic_prd
from trw_mcp.state.validation._prd_scoring_parsing import (
    _REQUIRED_SUBSECTIONS_BY_VARIANT,
    _extract_subheadings,
    _validation_profile,
)
from trw_mcp.state.validation.template_variants import get_required_sections

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

_AI_SECTION_KEYWORDS: list[str] = [
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


def score_structural_completeness(
    frontmatter: dict[str, object],
    sections: list[str],
    config: TRWConfig | None = None,
    category: str | None = None,
    content: str | None = None,
) -> DimensionScore:
    """Score the Structural Completeness dimension (15 points max).

    Checks: category-appropriate sections present, required frontmatter
    fields, confidence scores present (PRD-CORE-080-FR05). For AI/LLM/
    agentic PRDs, also scores AI operational subsections.
    """
    _config = config or get_config()
    max_score = _config.validation_structure_weight

    resolved_category = category or str(frontmatter.get("category", ""))
    required_sections = get_required_sections(resolved_category)

    expected = len(required_sections)
    found = min(len(sections), expected)
    section_ratio = found / expected

    required_fm_fields = ["id", "title", "version", "status", "priority"]
    fm_present = sum(1 for f in required_fm_fields if frontmatter.get(f))
    fm_ratio = fm_present / len(required_fm_fields)

    confidence = frontmatter.get("confidence", {})
    confidence_fields = ["implementation_feasibility", "requirement_clarity", "estimate_confidence"]
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

    ai_operational_sections_found = 0
    ai_operational_sections_expected = 7

    ai_operational_section_found = False
    if content is not None and _validation_profile(frontmatter) != "content_docs":
        ai_operational_section_found = _is_ai_agentic_prd(frontmatter, content)
        if ai_operational_section_found:
            present_subsections = _extract_subheadings(content)
            ai_operational_sections_found = sum(
                1 for kw in _AI_SECTION_KEYWORDS if any(kw.lower() in ss.lower() for ss in present_subsections)
            )
            subsection_ratio = (subsection_ratio * 0.75) + (
                ai_operational_sections_found / ai_operational_sections_expected * 0.25
            )

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
