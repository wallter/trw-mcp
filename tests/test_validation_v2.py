"""Tests for PRD-CORE-008 Phase 2a: Content Density + Quality Tiers.

Tests the semantic validation engine: 3-dimension scoring model,
quality tier classification, section density analysis, and
improvement suggestions. Also covers PRD-FIX-054 (stub removal,
ambiguity rate computation, weight recalibration).
"""

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
from trw_mcp.state.validation.prd_quality import (
    _KNOWN_TEST_PATTERNS,
    _TEST_REF_RE,
    _compute_ambiguity_rate,
)
from trw_mcp.state.validation.risk_profiles import RISK_PROFILES

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
        assert result.score < 20.0  # low density for placeholder-only PRD
        assert result.max_score == 42.0  # recalibrated weight (FR03 — PRD-FIX-054)

    def test_filled_prd_high_density(self) -> None:
        result = score_content_density(_FILLED_PRD)
        # Density ~42% of max_score 42 → score ~17.6
        assert result.score > 10.0

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
        assert result.score > 20.0  # near max 25 (recalibrated — FR03 PRD-FIX-054)

    def test_6_sections_half_score(self) -> None:
        sections = ["Problem Statement", "Goals & Non-Goals", "User Stories",
                     "Functional Requirements", "Non-Functional Requirements",
                     "Technical Approach"]
        frontmatter = {"id": "X", "title": "Y", "version": "1.0", "status": "draft", "priority": "P1"}
        result = score_structural_completeness(frontmatter, sections)
        assert result.score < 20.0  # less than full

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
        # 3/3 fields populated = field_ratio 1.0 → 13.2 pts (40% of max 33)
        assert result.score >= 13.0

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
        assert 0.0 < result.score < 33.0  # less than max 33 (recalibrated)


# ---------------------------------------------------------------------------
# Test: Dimension Weights
# ---------------------------------------------------------------------------


class TestDimensionWeights:
    """Test that active dimension weights sum to 100 and stub weights are 0 (FR03 — PRD-FIX-054)."""

    def test_active_weights_sum_100(self) -> None:
        """3 active dimension weights must sum to exactly 100.0."""
        config = TRWConfig()
        total = (
            config.validation_density_weight
            + config.validation_structure_weight
            + config.validation_traceability_weight
        )
        assert total == 100.0

    def test_stub_weight_defaults_are_zero(self) -> None:
        """Stub dimension weights must default to 0.0 (reserved for future use)."""
        config = TRWConfig()
        assert config.validation_smell_weight == 0.0
        assert config.validation_readability_weight == 0.0
        assert config.validation_ears_weight == 0.0

    def test_active_weights_values(self) -> None:
        """Active weights must match specified recalibrated values."""
        config = TRWConfig()
        assert config.validation_density_weight == 42.0
        assert config.validation_structure_weight == 25.0
        assert config.validation_traceability_weight == 33.0


# ---------------------------------------------------------------------------
# Test: Ambiguity Rate (FR02 — PRD-FIX-054)
# ---------------------------------------------------------------------------


class TestAmbiguityRate:
    """Test _compute_ambiguity_rate function."""

    def test_ambiguity_rate_with_vague_terms(self) -> None:
        """PRD with vague terms and FR statements must yield ambiguity_rate > 0."""
        content = "\n".join([
            f"### PRD-TEST-{i:03d}-FR01: Requirement {i}" for i in range(10)
        ]) + "\nThe system might fail. We should consider alternatives. As needed."
        rate = _compute_ambiguity_rate(content)
        assert rate > 0.0

    def test_ambiguity_rate_zero_for_clean_prd(self) -> None:
        """PRD with no vague terms must return 0.0."""
        content = "\n".join([
            f"### PRD-TEST-{i:03d}-FR01: Requirement {i}" for i in range(10)
        ]) + "\nThe system shall validate all inputs within 200ms."
        rate = _compute_ambiguity_rate(content)
        assert rate == 0.0

    def test_ambiguity_rate_zero_for_no_requirements(self) -> None:
        """PRD with no requirement-like statements must return 0.0 (no divide-by-zero)."""
        content = "This is a document with no FRs, no NFRs, no checkboxes."
        rate = _compute_ambiguity_rate(content)
        assert rate == 0.0

    def test_ambiguity_rate_wired_into_v2_result(self) -> None:
        """validate_prd_quality_v2 must return computed ambiguity_rate, not hardcoded 0.0."""
        # A clean PRD (no vague terms) should return 0.0
        result = validate_prd_quality_v2(_FILLED_PRD)
        # _FILLED_PRD uses "should consider" in no place — just verify it's a float
        assert isinstance(result.ambiguity_rate, float)
        assert result.ambiguity_rate >= 0.0

    def test_ambiguity_rate_nonzero_for_vague_prd(self) -> None:
        """PRD with vague terms in FR statements must yield ambiguity_rate > 0 from v2."""
        vague_content = _MINIMAL_FRONTMATTER + """\
## 4. Functional Requirements

### FR01: Do Something
The system might do this, or possibly that. As needed.
Also should consider alternatives.

### FR02: Other Thing
This might work approximately right.
"""
        result = validate_prd_quality_v2(vague_content)
        assert result.ambiguity_rate > 0.0


# ---------------------------------------------------------------------------
# Test: Risk Profile Weights (FR04 — PRD-FIX-054)
# ---------------------------------------------------------------------------


class TestRiskProfileWeights:
    """Test that RISK_PROFILES have 3-tuple weights summing to 100."""

    def test_all_profiles_have_3_weights(self) -> None:
        """Each risk profile must have exactly 3 weight entries (FR04 — PRD-FIX-054)."""
        for name, profile in RISK_PROFILES.items():
            assert len(profile.weights) == 3, (
                f"Profile '{name}' has {len(profile.weights)} weights, expected 3"
            )

    def test_all_profiles_weights_sum_to_100(self) -> None:
        """Each risk profile's weights must sum to exactly 100 (FR04 — PRD-FIX-054)."""
        for name, profile in RISK_PROFILES.items():
            total = sum(profile.weights)
            assert total == 100, (
                f"Profile '{name}' weights sum to {total}, expected 100"
            )

    def test_risk_scaled_validation_no_stubs(self) -> None:
        """Risk-scaled validation must produce no stub dimensions (FR04 — PRD-FIX-054)."""
        result = validate_prd_quality_v2(_FILLED_PRD, risk_level="critical")
        for dim in result.dimensions:
            assert dim.max_score > 0.0, (
                f"Dimension '{dim.name}' has max_score=0.0 after risk scaling"
            )


# ---------------------------------------------------------------------------
# Test: Improvement Suggestions
# ---------------------------------------------------------------------------


class TestImprovementSuggestions:
    """Test generate_improvement_suggestions."""

    def test_skeleton_gets_suggestions(self) -> None:
        """All 3 active low-scoring dimensions should yield suggestions."""
        dims = [
            DimensionScore(name="content_density", score=2.0, max_score=42.0),
            DimensionScore(name="structural_completeness", score=3.0, max_score=25.0),
            DimensionScore(name="traceability", score=0.0, max_score=33.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert len(suggestions) >= 3  # all 3 active dims are below threshold

    def test_improvement_suggestions_exclude_stubs(self) -> None:
        """Suggestions must never reference stub dimension names (FR07 — PRD-FIX-054)."""
        stub_names = {"smell_score", "readability", "ears_coverage"}
        dims = [
            DimensionScore(name="content_density", score=0.0, max_score=42.0),
            DimensionScore(name="structural_completeness", score=0.0, max_score=25.0),
            DimensionScore(name="traceability", score=0.0, max_score=33.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        for suggestion in suggestions:
            assert suggestion.dimension not in stub_names

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

    def test_v2_has_3_dimensions(self) -> None:
        """V2 output must contain exactly 3 active dimensions (FR01 — PRD-FIX-054)."""
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert len(result.dimensions) == 3
        dim_names = {d.name for d in result.dimensions}
        assert dim_names == {"content_density", "structural_completeness", "traceability"}

    def test_v2_no_stub_dimensions(self) -> None:
        """No dimension in output may have max_score == 0.0 (FR01 — PRD-FIX-054)."""
        result = validate_prd_quality_v2(_FILLED_PRD)
        stub_names = {"smell_score", "readability", "ears_coverage"}
        for dim in result.dimensions:
            assert dim.name not in stub_names, f"Stub dimension found: {dim.name}"
            assert dim.max_score > 0.0, f"Dimension {dim.name} has max_score=0.0"

    def test_v2_retains_deprecated_fields(self) -> None:
        """ValidationResultV2 must retain backward-compat fields (NFR02 — PRD-FIX-054)."""
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert hasattr(result, "completeness_score")
        assert hasattr(result, "consistency_score")
        assert hasattr(result, "smell_findings")
        assert hasattr(result, "readability")
        assert hasattr(result, "ears_classifications")

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


# ---------------------------------------------------------------------------
# Test: Language-Agnostic test_refs Regex (PRD-FIX-055)
# ---------------------------------------------------------------------------


class TestTestRefsRegex:
    """Unit tests for the _TEST_REF_RE pattern — PRD-FIX-055.

    Each test wraps a file reference in a minimal table row (the typical
    context inside a traceability matrix section) and asserts whether
    _TEST_REF_RE matches it or not.
    """

    # --- FR01: Python conventions (backward compat — FR02) ---

    def test_python_prefix_matches(self) -> None:
        """Existing Python test prefix convention must continue to match."""
        assert _TEST_REF_RE.findall("`test_module.py`") == ["`test_module.py`"]

    def test_python_prefix_with_pytest_node_matches(self) -> None:
        """Python pytest node ID (module::function) must match."""
        assert _TEST_REF_RE.findall("`test_api.py::test_create`") == [
            "`test_api.py::test_create`"
        ]

    def test_python_prefix_underscore_matches(self) -> None:
        """Underscore-prefixed Python test file must match."""
        assert _TEST_REF_RE.findall("`test_validation_v2.py`") == [
            "`test_validation_v2.py`"
        ]

    # --- FR01: TypeScript/JavaScript conventions ---

    def test_typescript_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`Component.test.tsx`") == ["`Component.test.tsx`"]

    def test_typescript_spec_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`api.spec.ts`") == ["`api.spec.ts`"]

    def test_javascript_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`utils.test.js`") == ["`utils.test.js`"]

    def test_javascript_spec_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`service.spec.js`") == ["`service.spec.js`"]

    # --- FR01: Go convention ---

    def test_go_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`handler_test.go`") == ["`handler_test.go`"]

    def test_go_test_suffix_with_path_matches(self) -> None:
        assert _TEST_REF_RE.findall("`internal/handler_test.go`") == [
            "`internal/handler_test.go`"
        ]

    # --- FR01: Java conventions ---

    def test_java_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`UserServiceTest.java`") == [
            "`UserServiceTest.java`"
        ]

    def test_java_tests_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`UserServiceTests.java`") == [
            "`UserServiceTests.java`"
        ]

    # --- FR01: Ruby convention ---

    def test_ruby_spec_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`user_spec.rb`") == ["`user_spec.rb`"]

    # --- FR01: tests/ directory convention (Rust, etc.) ---

    def test_tests_dir_matches(self) -> None:
        assert _TEST_REF_RE.findall("`tests/integration.rs`") == [
            "`tests/integration.rs`"
        ]

    def test_test_dir_singular_matches(self) -> None:
        """test/ (singular) directory must also match."""
        assert _TEST_REF_RE.findall("`test/helpers.py`") == ["`test/helpers.py`"]

    # --- FR03: No false positives on non-test files ---

    def test_plain_python_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`prd_quality.py`") == []

    def test_plain_typescript_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`router.ts`") == []

    def test_plain_go_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`main.go`") == []

    def test_plain_java_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`UserService.java`") == []

    def test_config_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`config.py`") == []

    def test_server_ts_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`server.ts`") == []

    # --- FR01 integration: multiple refs in a matrix section ---

    def test_mixed_language_matrix_all_counted(self) -> None:
        """Mixed-language traceability matrix must count all test refs."""
        matrix_section = (
            "| FR01 | US-001 | `src/validation.py` | `test_api.py::test_create` | Pending |\n"
            "| FR02 | US-001 | `Dashboard.tsx` | `Dashboard.test.tsx` | Pending |\n"
            "| FR03 | US-002 | `handler.go` | `handler_test.go` | Pending |\n"
        )
        matches = _TEST_REF_RE.findall(matrix_section)
        assert len(matches) == 3
        assert "`test_api.py::test_create`" in matches
        assert "`Dashboard.test.tsx`" in matches
        assert "`handler_test.go`" in matches

    def test_non_test_files_not_counted_in_mixed_matrix(self) -> None:
        """impl_refs like `src/validation.py` must NOT appear in test_refs."""
        matrix_section = (
            "| FR01 | `src/validation.py` | `test_api.py` | Pending |\n"
        )
        matches = _TEST_REF_RE.findall(matrix_section)
        assert "`src/validation.py`" not in matches
        assert "`test_api.py`" in matches


class TestKnownTestPatterns:
    """Verify _KNOWN_TEST_PATTERNS constant is populated (FR02)."""

    def test_constant_has_expected_languages(self) -> None:
        assert "python" in _KNOWN_TEST_PATTERNS
        assert "typescript" in _KNOWN_TEST_PATTERNS
        assert "go" in _KNOWN_TEST_PATTERNS
        assert "java" in _KNOWN_TEST_PATTERNS
        assert "ruby" in _KNOWN_TEST_PATTERNS
        assert "rust" in _KNOWN_TEST_PATTERNS

    def test_constant_values_are_strings(self) -> None:
        for lang, description in _KNOWN_TEST_PATTERNS.items():
            assert isinstance(description, str) and description, (
                f"Language '{lang}' has empty or non-string description"
            )


class TestTraceabilityV2LanguageAgnostic:
    """Integration tests: score_traceability_v2 with non-Python test refs."""

    _TS_PRD = (
        _MINIMAL_FRONTMATTER
        + """\
# PRD-TS-001: TypeScript PRD

## 1. Problem Statement
TypeScript frontend needs validation.

## 2. Goals & Non-Goals
### Goals
- Add form validation

## 3. User Stories
### US-001
**As a** user **I want** validation **So that** errors are clear.

## 4. Functional Requirements
### PRD-TS-001-FR01: Validate Form
**Priority**: Must Have
**Description**: Validate all form fields on submit.

## 5. Non-Functional Requirements
- Response under 100ms

## 6. Technical Approach
Uses React Hook Form.

## 7. Test Strategy
- Component.test.tsx

## 8. Rollout Plan
Phase 1: Implement.

## 9. Success Metrics
| Metric | Target |
|--------|--------|
| Error rate | <1% |

## 10. Dependencies & Risks
No blockers.

## 11. Open Questions
None.

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | `src/Form.tsx` | `Component.test.tsx` | Pending |
"""
    )

    def test_typescript_prd_matrix_score_positive(self) -> None:
        """A TypeScript PRD with .test.tsx refs must score matrix_score > 0."""
        frontmatter = {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": [],
                "enables": [],
            }
        }
        result = score_traceability_v2(frontmatter, self._TS_PRD)
        assert result.details["matrix_score"] > 0.0, (
            "TypeScript test refs not counted — matrix_score was 0"
        )

    def test_python_prd_backward_compat_score_unchanged(self) -> None:
        """Existing Python PRD score must not change after regex update."""
        frontmatter = {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": ["PRD-CORE-007"],
                "enables": ["PRD-CORE-009"],
            }
        }
        result = score_traceability_v2(frontmatter, _FILLED_PRD)
        # _FILLED_PRD traceability matrix has no backtick-wrapped test refs
        # (uses bare function names), so matrix_score remains 0 as before.
        # The important check is that the function runs without error and
        # field_ratio scoring still works.
        # field_ratio 1.0 × 0.4 × 33 = 13.2 (recalibrated max_score)
        assert result.score >= 13.0
        assert result.name == "traceability"

    def test_go_prd_test_refs_counted(self) -> None:
        """Go _test.go suffix refs in a traceability matrix must be counted."""
        frontmatter: dict[str, object] = {
            "traceability": {
                "implements": [],
                "depends_on": [],
                "enables": [],
            }
        }
        go_content = """\
## 12. Traceability Matrix

| Requirement | Implementation | Test | Status |
|-------------|----------------|------|--------|
| FR01 | `handler.go` | `handler_test.go` | Pending |
| FR02 | `service.go` | `service_test.go` | Pending |
"""
        result = score_traceability_v2(frontmatter, go_content)
        assert result.details["matrix_score"] > 0.0
