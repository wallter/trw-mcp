"""Tests for validation v2 density, tiers, and models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import (
    DimensionScore,
    ImprovementSuggestion,
    QualityTier,
    SectionScore,
    SmellFinding,
    ValidationResultV2,
)
from trw_mcp.state.validation import (
    classify_quality_tier,
    map_grade,
    score_content_density,
    score_section_density,
)

from ._validation_v2_support import _FILLED_PRD, _SKELETON_PRD


class TestSectionDensity:
    """Test score_section_density for individual sections."""

    def test_all_placeholders_scores_zero(self) -> None:
        body = "\n<!-- placeholder -->\n<!-- another -->\n"
        result = score_section_density("Test", body)
        assert result.density == 0.0
        assert result.substantive_lines == 0

    def test_all_substantive_scores_one(self) -> None:
        body = "\nThis is real content.\nMore real content here.\nAnd even more.\n"
        result = score_section_density("Test", body)
        assert result.density > 0.5
        assert result.substantive_lines >= 3

    def test_mixed_content(self) -> None:
        body = "\nReal content.\n\n<!-- placeholder -->\nMore content.\n\n"
        result = score_section_density("Test", body)
        assert 0.0 < result.density < 1.0

    def test_empty_section(self) -> None:
        result = score_section_density("Test", "")
        assert result.density == 0.0
        assert result.total_lines == 1

    def test_heading_only(self) -> None:
        body = "\n### Subsection\n"
        result = score_section_density("Test", body)
        assert result.substantive_lines == 0

    def test_section_name_preserved(self) -> None:
        result = score_section_density("Problem Statement", "content here")
        assert result.section_name == "Problem Statement"


class TestContentDensity:
    """Test score_content_density for full PRD content."""

    def test_skeleton_prd_low_density(self) -> None:
        result = score_content_density(_SKELETON_PRD)
        assert result.name == "content_density"
        assert result.score < 20.0
        assert result.max_score == 20.0

    def test_filled_prd_high_density(self) -> None:
        result = score_content_density(_FILLED_PRD)
        assert result.score > 8.0

    def test_weighted_average_sections(self) -> None:
        result = score_content_density(_FILLED_PRD)
        details = result.details
        assert "avg_density" in details
        assert "sections_scored" in details
        assert int(str(details["sections_scored"])) >= 10

    def test_custom_config_weight(self) -> None:
        config = TRWConfig(validation_density_weight=50.0)
        result = score_content_density(_FILLED_PRD, config=config)
        assert result.max_score == 50.0

    def test_no_sections_scores_zero(self) -> None:
        result = score_content_density("---\nprd:\n  id: X\n---\n\nNo sections here.")
        assert result.score == 0.0


class TestQualityTiers:
    """Test classify_quality_tier and map_grade."""

    def test_skeleton_tier(self) -> None:
        assert classify_quality_tier(0.0) == QualityTier.SKELETON
        assert classify_quality_tier(29.9) == QualityTier.SKELETON

    def test_draft_tier(self) -> None:
        assert classify_quality_tier(30.0) == QualityTier.DRAFT
        assert classify_quality_tier(59.9) == QualityTier.DRAFT

    def test_review_tier(self) -> None:
        assert classify_quality_tier(60.0) == QualityTier.REVIEW
        assert classify_quality_tier(84.9) == QualityTier.REVIEW

    def test_approved_tier(self) -> None:
        assert classify_quality_tier(85.0) == QualityTier.APPROVED
        assert classify_quality_tier(100.0) == QualityTier.APPROVED

    def test_boundary_30_is_draft(self) -> None:
        assert classify_quality_tier(30.0) == QualityTier.DRAFT

    def test_boundary_60_is_review(self) -> None:
        assert classify_quality_tier(60.0) == QualityTier.REVIEW

    def test_boundary_85_is_approved(self) -> None:
        assert classify_quality_tier(85.0) == QualityTier.APPROVED

    def test_custom_threshold(self) -> None:
        config = TRWConfig(validation_skeleton_threshold=50.0)
        assert classify_quality_tier(40.0, config=config) == QualityTier.SKELETON
        assert classify_quality_tier(50.0, config=config) == QualityTier.DRAFT

    def test_grade_mapping(self) -> None:
        assert map_grade(QualityTier.SKELETON) == "F"
        assert map_grade(QualityTier.DRAFT) == "D"
        assert map_grade(QualityTier.REVIEW) == "B"
        assert map_grade(QualityTier.APPROVED) == "A"

    def test_quality_tier_enum_has_4_members(self) -> None:
        assert len(QualityTier) == 4


class TestV2Models:
    """Test new Pydantic v2 models for CORE-008."""

    def test_section_score_defaults(self) -> None:
        ss = SectionScore(section_name="Test")
        assert ss.density == 0.0
        assert ss.substantive_lines == 0

    def test_section_score_rejects_negative_density(self) -> None:
        with pytest.raises(ValidationError):
            SectionScore(section_name="Test", density=-0.1)

    def test_dimension_score_bounds(self) -> None:
        ds = DimensionScore(name="test", score=10.0, max_score=15.0)
        assert ds.score == 10.0
        assert ds.max_score == 15.0

    def test_dimension_score_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            DimensionScore(name="test", score=-1.0, max_score=10.0)

    def test_v2_result_has_v1_fields(self) -> None:
        result = ValidationResultV2(valid=True)
        assert hasattr(result, "valid")
        assert hasattr(result, "failures")
        assert hasattr(result, "completeness_score")
        assert hasattr(result, "traceability_coverage")
        assert hasattr(result, "ambiguity_rate")
        assert hasattr(result, "consistency_score")

    def test_v2_result_total_score_range(self) -> None:
        result = ValidationResultV2(total_score=50.0)
        assert 0.0 <= result.total_score <= 100.0

    def test_v2_result_total_score_rejects_over_100(self) -> None:
        with pytest.raises(ValidationError):
            ValidationResultV2(total_score=101.0)

    def test_quality_tier_enum_values(self) -> None:
        assert QualityTier.SKELETON.value == "skeleton"
        assert QualityTier.DRAFT.value == "draft"
        assert QualityTier.REVIEW.value == "review"
        assert QualityTier.APPROVED.value == "approved"

    def test_improvement_suggestion_fields(self) -> None:
        s = ImprovementSuggestion(
            dimension="density",
            priority="high",
            message="Add content",
            current_score=5.0,
            potential_gain=20.0,
        )
        assert s.dimension == "density"
        assert s.potential_gain == 20.0

    def test_smell_finding_model(self) -> None:
        sf = SmellFinding(
            category="vague_terms",
            line_number=42,
            matched_text="fast",
            severity="warning",
            suggestion="Use specific metrics",
        )
        assert sf.category == "vague_terms"
        assert sf.line_number == 42
