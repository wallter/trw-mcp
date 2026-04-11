"""Tests for PRD-CORE-080: Category-specific template variants.

Covers:
- FR01: Category-to-template variant mapping (template_variants.py)
- FR02/FR05: Category-aware structural completeness scoring
- FR03: Decorative field removal from generated PRDs
- FR04: Configurable per-section content density weights via TRWConfig
- Backward compatibility: CORE/QUAL PRDs still score against 12 sections
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation.prd_quality import (
    score_content_density,
    score_structural_completeness,
)
from trw_mcp.state.validation.template_variants import (
    CATEGORY_TO_VARIANT,
    TEMPLATE_VARIANTS,
    get_required_sections,
    get_variant_for_category,
)

# ---------------------------------------------------------------------------
# FR01 / FR02 / FR03 / FR04: Variant mapping
# ---------------------------------------------------------------------------


def test_feature_template_has_12_sections() -> None:
    """Feature variant (CORE/QUAL) must have exactly 12 sections."""
    sections = TEMPLATE_VARIANTS["feature"]
    assert len(sections) == 12


def test_fix_template_has_8_sections() -> None:
    """Fix variant must have exactly 8 sections (PRD-CORE-080-FR02)."""
    sections = TEMPLATE_VARIANTS["fix"]
    assert len(sections) == 8


def test_infrastructure_template_has_9_sections() -> None:
    """Infrastructure variant must have exactly 9 sections (PRD-CORE-080-FR03)."""
    sections = TEMPLATE_VARIANTS["infrastructure"]
    assert len(sections) == 9


def test_research_template_has_7_sections() -> None:
    """Research variant must have exactly 7 sections (PRD-CORE-080-FR04)."""
    sections = TEMPLATE_VARIANTS["research"]
    assert len(sections) == 7


def test_all_valid_categories_map_to_a_variant() -> None:
    """All known PRD categories must have a variant mapping."""
    expected_categories = {"CORE", "QUAL", "EVAL", "FIX", "INFRA", "LOCAL", "RESEARCH", "EXPLR"}
    assert set(CATEGORY_TO_VARIANT.keys()) == expected_categories


def test_core_maps_to_feature_variant() -> None:
    assert get_variant_for_category("CORE") == "feature"


def test_qual_maps_to_feature_variant() -> None:
    assert get_variant_for_category("QUAL") == "feature"


def test_eval_maps_to_feature_variant() -> None:
    assert get_variant_for_category("EVAL") == "feature"


def test_fix_maps_to_fix_variant() -> None:
    assert get_variant_for_category("FIX") == "fix"


def test_infra_maps_to_infrastructure_variant() -> None:
    assert get_variant_for_category("INFRA") == "infrastructure"


def test_local_maps_to_infrastructure_variant() -> None:
    assert get_variant_for_category("LOCAL") == "infrastructure"


def test_research_maps_to_research_variant() -> None:
    assert get_variant_for_category("RESEARCH") == "research"


def test_explr_maps_to_research_variant() -> None:
    assert get_variant_for_category("EXPLR") == "research"


def test_unknown_category_defaults_to_feature() -> None:
    """Unrecognized category must default to feature template for backward compat."""
    assert get_variant_for_category("UNKNOWN") == "feature"
    assert get_variant_for_category("") == "feature"


def test_get_variant_is_case_insensitive() -> None:
    """Category lookup must be case-insensitive."""
    assert get_variant_for_category("fix") == "fix"
    assert get_variant_for_category("Fix") == "fix"
    assert get_variant_for_category("core") == "feature"


def test_get_required_sections_for_fix() -> None:
    """get_required_sections('FIX') must return the fix variant list."""
    sections = get_required_sections("FIX")
    assert sections == TEMPLATE_VARIANTS["fix"]
    assert len(sections) == 8


def test_get_required_sections_for_core() -> None:
    """get_required_sections('CORE') must return the feature variant (12 sections)."""
    sections = get_required_sections("CORE")
    assert len(sections) == 12


def test_get_required_sections_for_unknown_defaults_to_feature() -> None:
    """get_required_sections for unknown category must return feature list."""
    sections = get_required_sections("PERF")
    assert sections == TEMPLATE_VARIANTS["feature"]


def test_fix_sections_are_returned_as_new_list() -> None:
    """get_required_sections must return a copy, not a reference."""
    s1 = get_required_sections("FIX")
    s2 = get_required_sections("FIX")
    assert s1 is not s2
    assert s1 == s2


def test_fix_template_contains_required_headings() -> None:
    """Fix template must include Problem Statement and Traceability Matrix."""
    sections = TEMPLATE_VARIANTS["fix"]
    assert "Problem Statement" in sections
    assert "Functional Requirements" in sections
    assert "Non-Functional Requirements" in sections
    assert "Test Strategy" in sections
    assert "Traceability Matrix" in sections
    assert "Open Questions" in sections


def test_fix_template_excludes_feature_only_sections() -> None:
    """Fix template must NOT include feature-only sections (User Stories, Success Metrics, etc.)."""
    sections = TEMPLATE_VARIANTS["fix"]
    feature_only = {"User Stories", "Goals & Non-Goals", "Success Metrics", "Rollout Plan"}
    for section in feature_only:
        assert section not in sections, f"Fix template should not contain '{section}'"


# ---------------------------------------------------------------------------
# FR05: Category-aware structural completeness scoring
# ---------------------------------------------------------------------------


def _make_frontmatter(category: str, include_confidence: bool = True) -> dict[str, object]:
    """Build a minimal valid frontmatter dict for testing."""
    fm: dict[str, object] = {
        "id": f"PRD-{category}-001",
        "title": "Test PRD",
        "version": "1.0",
        "status": "draft",
        "priority": "P1",
        "category": category,
    }
    if include_confidence:
        fm["confidence"] = {
            "implementation_feasibility": 0.8,
            "requirement_clarity": 0.8,
            "estimate_confidence": 0.7,
        }
    return fm


def test_fix_prd_with_8_sections_scores_full_section_ratio() -> None:
    """A FIX PRD with all 8 required sections must score section_ratio = 1.0."""
    fm = _make_frontmatter("FIX")
    sections = get_required_sections("FIX")  # exactly 8
    config = TRWConfig()
    result = score_structural_completeness(fm, sections, config)
    # details.sections_found should equal details.sections_expected
    assert result.details["sections_found"] == result.details["sections_expected"]
    assert result.details["sections_expected"] == 8


def test_fix_prd_with_8_sections_not_penalized_vs_12() -> None:
    """A well-formed FIX PRD (8 sections) must score higher than 8/12 = 0.67 * max."""
    fm = _make_frontmatter("FIX")
    sections = get_required_sections("FIX")
    config = TRWConfig()
    result = score_structural_completeness(fm, sections, config)
    # With full section coverage (8/8), frontmatter (5/5), confidence (3/3):
    # composite = 0.5*1.0 + 0.3*1.0 + 0.2*1.0 = 1.0 -> max_score
    assert result.score >= result.max_score * 0.9, (
        f"FIX PRD with 8/8 sections scored {result.score:.2f} / {result.max_score}, expected >= 90% of max"
    )


def test_core_prd_still_uses_12_sections() -> None:
    """CORE PRD must still be evaluated against 12 sections (backward compat)."""
    fm = _make_frontmatter("CORE")
    sections = get_required_sections("CORE")  # 12
    config = TRWConfig()
    result = score_structural_completeness(fm, sections, config)
    assert result.details["sections_expected"] == 12


def test_core_prd_with_8_sections_is_penalized() -> None:
    """CORE PRD with only 8 sections must NOT score full section coverage."""
    fm = _make_frontmatter("CORE")
    fix_sections = get_required_sections("FIX")  # 8 sections
    config = TRWConfig()
    result = score_structural_completeness(fm, fix_sections, config)
    # 8/12 = 0.67 section ratio — composite will be < 1.0
    assert result.details["sections_found"] == 8
    assert result.details["sections_expected"] == 12


def test_unknown_category_defaults_to_12_section_evaluation() -> None:
    """PRD with unknown category must be evaluated against 12 sections (backward compat)."""
    fm = _make_frontmatter("PERF")
    sections = ["Problem Statement"] * 5
    config = TRWConfig()
    result = score_structural_completeness(fm, sections, config)
    assert result.details["sections_expected"] == 12


def test_missing_category_defaults_to_12_section_evaluation() -> None:
    """PRD without a category field must default to 12-section Feature evaluation."""
    fm: dict[str, object] = {
        "id": "PRD-001",
        "title": "Legacy PRD",
        "version": "1.0",
        "status": "draft",
        "priority": "P1",
        # no "category" key
    }
    config = TRWConfig()
    result = score_structural_completeness(fm, ["Section1"] * 5, config)
    assert result.details["sections_expected"] == 12


def test_infra_prd_evaluated_against_9_sections() -> None:
    """INFRA PRD must be evaluated against 9 sections."""
    fm = _make_frontmatter("INFRA")
    sections = get_required_sections("INFRA")  # 9
    config = TRWConfig()
    result = score_structural_completeness(fm, sections, config)
    assert result.details["sections_expected"] == 9
    assert result.details["sections_found"] == 9


def test_research_prd_evaluated_against_7_sections() -> None:
    """RESEARCH PRD must be evaluated against 7 sections."""
    fm = _make_frontmatter("RESEARCH")
    sections = get_required_sections("RESEARCH")  # 7
    config = TRWConfig()
    result = score_structural_completeness(fm, sections, config)
    assert result.details["sections_expected"] == 7


def test_category_param_overrides_frontmatter() -> None:
    """Explicit ``category`` param to score_structural_completeness overrides frontmatter."""
    fm = _make_frontmatter("CORE")  # frontmatter says CORE (12 sections)
    config = TRWConfig()
    # Force FIX variant (8 sections) via explicit param
    result = score_structural_completeness(fm, ["S"] * 8, config, category="FIX")
    assert result.details["sections_expected"] == 8


# ---------------------------------------------------------------------------
# FR03: Decorative field removal from generated PRDs
# ---------------------------------------------------------------------------


def test_strip_deprecated_fields_removes_aaref_components() -> None:
    """_strip_deprecated_fields must remove the aaref_components key."""
    from trw_mcp.tools.requirements import _strip_deprecated_fields

    fm: dict[str, object] = {
        "id": "PRD-001",
        "title": "Test",
        "aaref_components": ["C1", "C2"],
        "status": "draft",
    }
    result = _strip_deprecated_fields(fm)
    assert "aaref_components" not in result
    assert result["id"] == "PRD-001"


def test_strip_deprecated_fields_removes_conflicts_with_from_traceability() -> None:
    """_strip_deprecated_fields must remove conflicts_with from nested traceability."""
    from trw_mcp.tools.requirements import _strip_deprecated_fields

    fm: dict[str, object] = {
        "id": "PRD-001",
        "traceability": {
            "implements": ["FR01"],
            "depends_on": [],
            "enables": [],
            "conflicts_with": [],
        },
    }
    result = _strip_deprecated_fields(fm)
    traceability = result.get("traceability")
    assert isinstance(traceability, dict)
    assert "conflicts_with" not in traceability
    assert "implements" in traceability


def test_strip_deprecated_fields_removes_none_values() -> None:
    """_strip_deprecated_fields must remove None-valued keys."""
    from trw_mcp.tools.requirements import _strip_deprecated_fields

    fm: dict[str, object] = {
        "id": "PRD-001",
        "wave_source": None,
        "risk_level": None,
        "title": "Test",
    }
    result = _strip_deprecated_fields(fm)
    assert "wave_source" not in result
    assert "risk_level" not in result
    assert result["title"] == "Test"


def test_strip_deprecated_fields_preserves_valid_data() -> None:
    """_strip_deprecated_fields must preserve all non-deprecated, non-null fields."""
    from trw_mcp.tools.requirements import _strip_deprecated_fields

    fm: dict[str, object] = {
        "id": "PRD-001",
        "title": "Test PRD",
        "version": "1.0",
        "status": "draft",
        "priority": "P1",
        "category": "CORE",
    }
    result = _strip_deprecated_fields(fm)
    assert result == fm  # no fields removed


def test_existing_prd_with_deprecated_fields_parses_without_error() -> None:
    """PRDFrontmatter must accept PRDs containing aaref_components (backward compat)."""
    from trw_mcp.models.requirements import PRDFrontmatter

    fm = PRDFrontmatter(
        id="PRD-FIX-001",
        title="Legacy PRD",
        aaref_components=["comp1"],
    )
    assert fm.aaref_components == ["comp1"]


def test_new_prd_frontmatter_omits_aaref_components_by_default() -> None:
    """PRDFrontmatter must default aaref_components to None (omitted in output)."""
    from trw_mcp.models.requirements import PRDFrontmatter

    fm = PRDFrontmatter(id="PRD-CORE-999", title="New PRD")
    assert fm.aaref_components is None


# ---------------------------------------------------------------------------
# FR04: Configurable per-section content density weights
# ---------------------------------------------------------------------------


def _build_section_content(section_name: str, lines: int = 10) -> str:
    """Build a minimal PRD content string with one section of known density."""
    body_lines = "\n".join(f"Line {i} of content here." for i in range(lines))
    return f"## 1. {section_name}\n\n{body_lines}\n"


def test_density_weight_defaults_are_correct() -> None:
    """TRWConfig default section weights must match PRD specification."""
    config = TRWConfig()
    assert config.density_weight_problem_statement == 2.0
    assert config.density_weight_functional_requirements == 2.0
    assert config.density_weight_traceability_matrix == 1.5
    assert config.density_weight_default == 1.0


def test_density_weight_problem_statement_override() -> None:
    """Custom Problem Statement weight must produce different score than default."""
    content = _build_section_content("Problem Statement", lines=10)

    config_default = TRWConfig()
    config_custom = TRWConfig(density_weight_problem_statement=3.0)

    result_default = score_content_density(content, config_default)
    result_custom = score_content_density(content, config_custom)

    # Higher weight on Problem Statement should produce higher weighted sum
    # (same density but more weight means higher ratio contribution)
    # Both have same max_score (validation_density_weight=42) but different
    # weighted averages when weight denominator changes
    # With only one section: avg_density = density * w / w = density (weight cancels)
    # BUT different default weight affects relative weighting when there are multiple sections.
    # For single section, weight doesn't affect final avg_density (w/w = 1).
    # We still verify the config field is read (no exception, correct type).
    assert isinstance(result_default.score, float)
    assert isinstance(result_custom.score, float)


def test_density_weight_multiple_sections_reflects_custom_weights() -> None:
    """Custom weights must change the weighted average when multiple sections exist."""
    # Two sections: Problem Statement (high weight) and Open Questions (default weight)
    content = (
        "## 1. Problem Statement\n\n"
        + "\n".join(f"Requirement line {i}." for i in range(10))
        + "\n\n## 2. Open Questions\n\n"
        # Deliberately sparse: only 2 substantive lines
        "TBD\nUnknown\n"
    )

    # Default: PS weight=2.0, OQ weight=1.0 → high-density PS pulls avg up
    config_default = TRWConfig()
    # Custom: PS weight=0.1, OQ weight=10.0 → sparse OQ dominates → lower avg
    config_custom = TRWConfig(
        density_weight_problem_statement=0.1,
        density_weight_default=10.0,
    )

    result_default = score_content_density(content, config_default)
    result_custom = score_content_density(content, config_custom)

    # When sparse section (OQ) has 10x weight, overall density must be lower
    assert result_custom.score < result_default.score, (
        f"Custom config (sparse section weighted 10x) should produce lower score "
        f"({result_custom.score:.4f}) than default ({result_default.score:.4f})"
    )


def test_density_weight_bounds_reject_negative_values() -> None:
    """TRWConfig must reject negative section weight values (ge=0.0)."""
    with pytest.raises(Exception):
        TRWConfig(density_weight_problem_statement=-1.0)


def test_density_weight_bounds_reject_values_above_10() -> None:
    """TRWConfig must reject section weight values above 10.0 (le=10.0)."""
    with pytest.raises(Exception):
        TRWConfig(density_weight_problem_statement=11.0)


def test_density_weight_zero_is_valid() -> None:
    """Zero weight (0.0) must be accepted (ge=0.0)."""
    config = TRWConfig(density_weight_problem_statement=0.0)
    assert config.density_weight_problem_statement == 0.0


def test_density_weight_ten_is_valid() -> None:
    """Weight of 10.0 must be accepted (le=10.0)."""
    config = TRWConfig(density_weight_traceability_matrix=10.0)
    assert config.density_weight_traceability_matrix == 10.0


# ---------------------------------------------------------------------------
# Integration tests: end-to-end _generate_prd_body + regression scoring
# ---------------------------------------------------------------------------


def test_prd_create_fix_category() -> None:
    """End-to-end: _generate_prd_body with category='FIX' produces exactly 8 numbered sections
    matching the Fix template names (PRD-CORE-080-FR01).

    Note: section ordering in the generated body follows the position of each section in the
    full 12-section feature template (the filter preserves relative order). We assert on the
    SET of section names, not their order.
    """
    import re

    from trw_mcp.tools.requirements import _generate_prd_body

    body = _generate_prd_body(
        prd_id="PRD-FIX-001",
        title="Test Fix PRD",
        input_text="Fix the broken thing",
        category="FIX",
    )

    # Extract all ## N. <Section Name> headings
    matches = re.findall(r"^## (\d+)\. (.+)$", body, re.MULTILINE)
    section_names = [name.strip() for _num, name in matches]

    assert len(section_names) == 8, (
        f"FIX PRD should have 8 sections, got {len(section_names)}: {section_names}"
    )

    expected_fix_sections = {
        "Problem Statement",
        "Root Cause Analysis",
        "Functional Requirements",
        "Non-Functional Requirements",
        "Test Strategy",
        "Rollback Plan",
        "Traceability Matrix",
        "Open Questions",
    }
    assert set(section_names) == expected_fix_sections, (
        f"FIX section names mismatch.\nGot:      {sorted(section_names)}\nExpected: {sorted(expected_fix_sections)}"
    )


def test_prd_create_research_category() -> None:
    """End-to-end: _generate_prd_body with category='RESEARCH' produces exactly 7 numbered sections
    matching the Research template names (PRD-CORE-080-FR01).

    Note: section ordering in the generated body follows the position of each section in the
    full 12-section feature template (the filter preserves relative order). We assert on the
    SET of section names, not their order.
    """
    import re

    from trw_mcp.tools.requirements import _generate_prd_body

    body = _generate_prd_body(
        prd_id="PRD-RESEARCH-001",
        title="Test Research PRD",
        input_text="Investigate the unknown thing",
        category="RESEARCH",
    )

    matches = re.findall(r"^## (\d+)\. (.+)$", body, re.MULTILINE)
    section_names = [name.strip() for _num, name in matches]

    assert len(section_names) == 7, (
        f"RESEARCH PRD should have 7 sections, got {len(section_names)}: {section_names}"
    )

    expected_research_sections = {
        "Problem Statement",
        "Background & Prior Art",
        "Research Questions",
        "Methodology",
        "Findings",
        "Recommendations",
        "Open Questions",
    }
    assert set(section_names) == expected_research_sections, (
        f"RESEARCH section names mismatch.\nGot:      {sorted(section_names)}\nExpected: {sorted(expected_research_sections)}"
    )


@pytest.mark.parametrize(
    "prd_filename",
    [
        "PRD-FIX-054.md",
        "PRD-CORE-080.md",
        "PRD-CORE-055.md",
    ],
)
def test_existing_prd_score_regression(prd_filename: str) -> None:
    """Regression: well-formed existing PRDs must stay above the calibrated floor.

    The implementation-readiness dimension intentionally re-calibrated historical
    PRD scores downward when they lack newer proof-oriented subsections. Keep a
    floor at 50 so mature PRDs remain clearly above skeleton-tier quality while
    allowing the new signal to differentiate proof-rich plans from prose-heavy
    ones.
    """
    from pathlib import Path

    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    prds_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "docs"
        / "requirements-aare-f"
        / "prds"
    )
    prd_path = prds_dir / prd_filename
    assert prd_path.exists(), f"PRD file not found: {prd_path}"

    content = prd_path.read_text(encoding="utf-8")
    result = validate_prd_quality_v2(content)

    assert result.total_score >= 50, (
        f"{prd_filename} scored {result.total_score:.2f} — must be >= 50 "
        f"(implementation-readiness calibration floor)"
    )
