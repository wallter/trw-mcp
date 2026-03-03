"""Tests for PRD-CORE-008 Phase 2a: Content Density + Quality Tiers.

Tests the semantic validation engine: 6-dimension scoring model,
quality tier classification, section density analysis, and
improvement suggestions.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import (
    DimensionScore,
    ImprovementSuggestion,
    QualityTier,
    SectionScore,
    SmellFinding,
    ValidationResult,
    ValidationResultV2,
)
from trw_mcp.state.validation import (
    classify_quality_tier,
    generate_improvement_suggestions,
    map_grade,
    score_content_density,
    score_section_density,
    score_structural_completeness,
    score_traceability_v2,
    validate_prd_quality,
    validate_prd_quality_v2,
)

# ---------------------------------------------------------------------------
# Fixtures: PRD content templates
# ---------------------------------------------------------------------------

_MINIMAL_FRONTMATTER = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE
  confidence:
    implementation_feasibility: 0.85
    requirement_clarity: 0.80
    estimate_confidence: 0.75
  traceability:
    implements: []
    depends_on: [PRD-CORE-007]
    enables: [PRD-CORE-009]
---
"""

_SKELETON_PRD = _MINIMAL_FRONTMATTER + """\
# PRD-TEST-001: Test PRD

## 1. Problem Statement
<!-- Describe the problem -->

## 2. Goals & Non-Goals
<!-- List goals -->

## 3. User Stories
<!-- User stories -->

## 4. Functional Requirements
<!-- Requirements -->

## 5. Non-Functional Requirements
<!-- NFRs -->

## 6. Technical Approach
<!-- Architecture -->

## 7. Test Strategy
<!-- Tests -->

## 8. Rollout Plan
<!-- Rollout -->

## 9. Success Metrics
<!-- Metrics -->

## 10. Dependencies & Risks
<!-- Dependencies -->

## 11. Open Questions
<!-- Questions -->

## 12. Traceability Matrix
<!-- Matrix -->
"""

_FILLED_PRD = _MINIMAL_FRONTMATTER + """\
# PRD-TEST-001: Test PRD

## 1. Problem Statement

### Background
The current system lacks proper validation. Users report frequent errors
when submitting forms. The error handling module in src/errors.py has not
been updated since version 1.0 and does not cover the new API endpoints.

### Problem
Form validation fails silently, causing data corruption in 5% of submissions.

### Impact
Users lose trust in the system. Support tickets increased 40% in Q1.

## 2. Goals & Non-Goals

### Goals
- Implement comprehensive form validation with specific error messages
- Reduce data corruption rate from 5% to below 0.1%
- Add validation feedback within 200ms response time

### Non-Goals
- Redesigning the entire form UI
- Migrating to a new validation library

## 3. User Stories

### US-001: Form Validation Feedback
**As a** user
**I want** to see specific validation errors when I submit a form
**So that** I can fix my input without guessing what went wrong

**Acceptance Criteria**:
- Given invalid email format, When submitted, Then show "Invalid email format"
- Given missing required field, When submitted, Then highlight the field in red

## 4. Functional Requirements

### PRD-TEST-001-FR01: Input Validation
**Priority**: Must Have
**Description**: When the user submits a form, the system shall validate all
required fields and return specific error messages for each invalid field.
The validation shall complete within 200ms.
**Acceptance**: All required fields are validated. Error messages are specific.

### PRD-TEST-001-FR02: Error Display
**Priority**: Must Have
**Description**: The system shall display validation errors inline next to
the corresponding form fields. Each error message shall be descriptive
and suggest how to fix the issue.
**Acceptance**: Errors appear next to fields. Messages include fix suggestions.

## 5. Non-Functional Requirements

### NFR01: Performance
- Form validation shall complete within 200ms for forms with up to 50 fields
- No external API calls during client-side validation

### NFR02: Accessibility
- Error messages shall be readable by screen readers
- Color is not the only indicator of errors (also uses icons and text)

## 6. Technical Approach

### Architecture Impact
This change modifies the validation middleware in src/validation.py and adds
a new error display component in src/components/ErrorDisplay.tsx.

### Key Files
| File | Changes |
|------|---------|
| `src/validation.py` | Add field-level validation rules |
| `src/components/ErrorDisplay.tsx` | New inline error component |

## 7. Test Strategy

### Unit Tests
- test_validate_required_field_missing
- test_validate_email_format_invalid
- test_validate_within_200ms
- test_error_display_renders_message

### Integration Tests
- test_form_submission_with_errors
- test_form_submission_all_valid

## 8. Rollout Plan

### Phase 1: Validation Logic (1 session)
1. Add validation rules to src/validation.py
2. Write 10 unit tests
3. Verify 200ms performance target

### Phase 2: Error Display (1 session)
1. Create ErrorDisplay component
2. Wire into form submission flow
3. Write integration tests

## 9. Success Metrics

| Metric | Target | Method |
|--------|--------|--------|
| Data corruption rate | <0.1% | Monitor submissions |
| Validation latency | <200ms | Performance tests |
| Support tickets | -30% | Ticket count |

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status | Blocking |
|----|-------------|--------|----------|
| DEP-001 | React 18+ for concurrent rendering | Available | No |

### Risks
| ID | Risk | Probability | Impact | Mitigation |
|----|------|-------------|--------|------------|
| RISK-001 | Validation rules incomplete | Medium | High | Incremental rollout |

## 11. Open Questions

- Should we validate on blur or only on submit?
- How do we handle dynamic form fields added via JavaScript?

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 (Input Validation) | US-001 | `src/validation.py:validate_form()` | `test_validate_required_field_missing` | Pending |
| FR02 (Error Display) | US-001 | `src/components/ErrorDisplay.tsx` | `test_error_display_renders_message` | Pending |
"""

_PARTIAL_PRD = _MINIMAL_FRONTMATTER + """\
# PRD-TEST-001: Test PRD

## 1. Problem Statement

### Background
The system needs better error handling. This is important for reliability.

### Problem
Errors are not handled consistently across modules.

## 2. Goals & Non-Goals

### Goals
- Improve error handling across the codebase

## 3. User Stories
<!-- TODO: Add user stories -->

## 4. Functional Requirements

### PRD-TEST-001-FR01: Error Handler
**Priority**: Must Have
**Description**: The system shall handle errors consistently.

## 5. Non-Functional Requirements
<!-- TODO -->

## 6. Technical Approach
<!-- TODO -->

## 7. Test Strategy
- test_error_handling

## 8. Rollout Plan
<!-- TODO -->

## 9. Success Metrics
<!-- TODO -->

## 10. Dependencies & Risks
<!-- TODO -->

## 11. Open Questions
- What error handling strategy should we use?

## 12. Traceability Matrix
<!-- TODO: Fill in matrix -->
"""


# ---------------------------------------------------------------------------
# Test: Section Density
# ---------------------------------------------------------------------------


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
        # "".split("\n") produces [""], so total_lines == 1
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
        assert result.score < 10.0  # low density for placeholder-only PRD
        assert result.max_score == 25.0

    def test_filled_prd_high_density(self) -> None:
        result = score_content_density(_FILLED_PRD)
        # Density ~42% yields ~10.7/25 with section weighting
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


# ---------------------------------------------------------------------------
# Test: Quality Tiers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Test: Models
# ---------------------------------------------------------------------------


class TestV2Models:
    """Test new Pydantic v2 models for CORE-008."""

    def test_section_score_defaults(self) -> None:
        ss = SectionScore(section_name="Test")
        assert ss.density == 0.0
        assert ss.substantive_lines == 0

    def test_section_score_rejects_negative_density(self) -> None:
        with pytest.raises(Exception):
            SectionScore(section_name="Test", density=-0.1)

    def test_dimension_score_bounds(self) -> None:
        ds = DimensionScore(name="test", score=10.0, max_score=15.0)
        assert ds.score == 10.0
        assert ds.max_score == 15.0

    def test_dimension_score_rejects_negative(self) -> None:
        with pytest.raises(Exception):
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
        with pytest.raises(Exception):
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


# ---------------------------------------------------------------------------
# Test: Structural Completeness
# ---------------------------------------------------------------------------


class TestStructuralCompleteness:
    """Test score_structural_completeness."""

    def test_12_sections_full_score(self) -> None:
        from trw_mcp.state.prd_utils import extract_sections

        sections = extract_sections(_FILLED_PRD)
        frontmatter = {
            "id": "PRD-TEST-001",
            "title": "Test",
            "version": "1.0",
            "status": "draft",
            "priority": "P1",
            "confidence": {
                "implementation_feasibility": 0.85,
                "requirement_clarity": 0.80,
                "estimate_confidence": 0.75,
            },
        }
        result = score_structural_completeness(frontmatter, sections)
        assert result.name == "structural_completeness"
        assert result.score > 12.0  # near max 15

    def test_6_sections_half_score(self) -> None:
        sections = ["Problem Statement", "Goals & Non-Goals", "User Stories",
                     "Functional Requirements", "Non-Functional Requirements",
                     "Technical Approach"]
        frontmatter = {"id": "X", "title": "Y", "version": "1.0", "status": "draft", "priority": "P1"}
        result = score_structural_completeness(frontmatter, sections)
        assert result.score < 12.0  # less than full

    def test_missing_confidence_reduces_score(self) -> None:
        sections = extract_all_12_section_names()
        with_conf = score_structural_completeness(
            {"id": "X", "title": "Y", "version": "1.0", "status": "d", "priority": "P1",
             "confidence": {"implementation_feasibility": 0.8, "requirement_clarity": 0.8, "estimate_confidence": 0.7}},
            sections,
        )
        without_conf = score_structural_completeness(
            {"id": "X", "title": "Y", "version": "1.0", "status": "d", "priority": "P1"},
            sections,
        )
        assert with_conf.score > without_conf.score


def extract_all_12_section_names() -> list[str]:
    """Return list of 12 expected AARE-F section names."""
    return [
        "Problem Statement", "Goals & Non-Goals", "User Stories",
        "Functional Requirements", "Non-Functional Requirements",
        "Technical Approach", "Test Strategy", "Rollout Plan",
        "Success Metrics", "Dependencies & Risks", "Open Questions",
        "Traceability Matrix",
    ]


# ---------------------------------------------------------------------------
# Test: Traceability
# ---------------------------------------------------------------------------


class TestTraceabilityV2:
    """Test score_traceability_v2."""

    def test_full_traces_high_score(self) -> None:
        frontmatter = {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": ["PRD-CORE-007"],
                "enables": ["PRD-CORE-009"],
            }
        }
        result = score_traceability_v2(frontmatter, _FILLED_PRD)
        # 3/3 fields populated = field_ratio 1.0 → 8.0 pts (matrix not in fixture)
        assert result.score >= 8.0

    def test_no_traces_zero_score(self) -> None:
        frontmatter: dict[str, object] = {"traceability": {"implements": [], "depends_on": [], "enables": []}}
        # Content with no matrix
        content = "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\nNo content."
        result = score_traceability_v2(frontmatter, content)
        assert result.score == 0.0

    def test_partial_traces(self) -> None:
        frontmatter = {
            "traceability": {
                "implements": [],
                "depends_on": ["PRD-CORE-007"],
                "enables": [],
            }
        }
        result = score_traceability_v2(frontmatter, _PARTIAL_PRD)
        assert 0.0 < result.score < 15.0


# ---------------------------------------------------------------------------
# Test: Dimension Weights
# ---------------------------------------------------------------------------


class TestDimensionWeights:
    """Test that dimension weights sum to 100."""

    def test_default_weights_sum_100(self) -> None:
        config = TRWConfig()
        total = (
            config.validation_density_weight
            + config.validation_structure_weight
            + config.validation_traceability_weight
            + config.validation_smell_weight
            + config.validation_readability_weight
            + config.validation_ears_weight
        )
        assert total == 100.0


# ---------------------------------------------------------------------------
# Test: Improvement Suggestions
# ---------------------------------------------------------------------------


class TestImprovementSuggestions:
    """Test generate_improvement_suggestions."""

    def test_skeleton_gets_suggestions(self) -> None:
        dims = [
            DimensionScore(name="content_density", score=2.0, max_score=25.0),
            DimensionScore(name="structural_completeness", score=3.0, max_score=15.0),
            DimensionScore(name="traceability", score=0.0, max_score=20.0),
            DimensionScore(name="smell_score", score=15.0, max_score=15.0),
            DimensionScore(name="readability", score=5.0, max_score=10.0),
            DimensionScore(name="ears_coverage", score=0.0, max_score=15.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert len(suggestions) >= 4  # at least 4 low-scoring dims

    def test_suggestions_sorted_by_gain(self) -> None:
        dims = [
            DimensionScore(name="a", score=0.0, max_score=25.0),
            DimensionScore(name="b", score=0.0, max_score=10.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert suggestions[0].potential_gain >= suggestions[1].potential_gain

    def test_max_5_suggestions(self) -> None:
        dims = [
            DimensionScore(name=f"dim_{i}", score=0.0, max_score=20.0)
            for i in range(8)
        ]
        suggestions = generate_improvement_suggestions(dims, max_suggestions=5)
        assert len(suggestions) <= 5

    def test_high_scoring_no_suggestions(self) -> None:
        dims = [
            DimensionScore(name="a", score=20.0, max_score=25.0),
            DimensionScore(name="b", score=14.0, max_score=15.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Test: Full V2 Pipeline
# ---------------------------------------------------------------------------


class TestValidatePrdQualityV2:
    """Test the full validate_prd_quality_v2 orchestrator."""

    def test_skeleton_prd_detection(self) -> None:
        result = validate_prd_quality_v2(_SKELETON_PRD)
        # Skeleton PRD has all 12 section headings + full frontmatter but
        # placeholder-only content → structure/traceability carry it to DRAFT
        assert result.quality_tier == QualityTier.DRAFT
        assert result.total_score < 60.0
        assert result.grade == "D"

    def test_filled_prd_scores_above_draft(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert result.total_score > 30.0
        assert result.quality_tier in (QualityTier.DRAFT, QualityTier.REVIEW, QualityTier.APPROVED)

    def test_partial_prd_scores_draft_tier(self) -> None:
        result = validate_prd_quality_v2(_PARTIAL_PRD)
        assert result.quality_tier in (QualityTier.SKELETON, QualityTier.DRAFT)

    def test_v2_populates_v1_fields(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert hasattr(result, "valid")
        assert hasattr(result, "failures")
        assert hasattr(result, "completeness_score")

    def test_v2_has_6_dimensions(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert len(result.dimensions) == 6
        dim_names = {d.name for d in result.dimensions}
        assert dim_names == {
            "content_density", "structural_completeness", "traceability",
            "smell_score", "readability", "ears_coverage",
        }

    def test_v2_total_score_range(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert 0.0 <= result.total_score <= 100.0

    def test_v2_section_scores_populated(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert len(result.section_scores) >= 10

    def test_v2_improvement_suggestions(self) -> None:
        result = validate_prd_quality_v2(_SKELETON_PRD)
        assert len(result.improvement_suggestions) >= 1

    def test_backward_compat_v1_unchanged(self) -> None:
        """validate_prd_quality() still returns ValidationResult, not V2."""
        v1 = validate_prd_quality(
            {"id": "X", "title": "Y", "version": "1.0", "status": "draft", "priority": "P1"},
            ["Problem Statement"],
        )
        assert isinstance(v1, ValidationResult)
        assert not isinstance(v1, ValidationResultV2)

    def test_config_density_weight_override(self) -> None:
        config = TRWConfig(validation_density_weight=50.0, risk_scaling_enabled=False)
        result = validate_prd_quality_v2(_FILLED_PRD, config=config)
        density_dim = next(d for d in result.dimensions if d.name == "content_density")
        assert density_dim.max_score == 50.0

    def test_config_threshold_override(self) -> None:
        config = TRWConfig(validation_skeleton_threshold=80.0, risk_scaling_enabled=False)
        result = validate_prd_quality_v2(_PARTIAL_PRD, config=config)
        # With threshold at 80, more PRDs should be SKELETON
        assert result.quality_tier == QualityTier.SKELETON
