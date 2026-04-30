"""Tests for validation v2 ambiguity, risk profiles, and suggestions."""

from __future__ import annotations

from trw_mcp.models.requirements import DimensionScore
from trw_mcp.state.validation import generate_improvement_suggestions, validate_prd_quality_v2
from trw_mcp.state.validation.prd_quality import _compute_ambiguity_rate
from trw_mcp.state.validation.risk_profiles import RISK_PROFILES

from ._validation_v2_support import _FILLED_PRD, _MINIMAL_FRONTMATTER


class TestAmbiguityRate:
    """Test _compute_ambiguity_rate function."""

    def test_ambiguity_rate_with_vague_terms(self) -> None:
        content = (
            "\n".join([f"### PRD-TEST-{i:03d}-FR01: Requirement {i}" for i in range(10)])
            + "\nThe system might fail. We should consider alternatives. As needed."
        )
        rate = _compute_ambiguity_rate(content)
        assert rate > 0.0

    def test_ambiguity_rate_zero_for_clean_prd(self) -> None:
        content = (
            "\n".join([f"### PRD-TEST-{i:03d}-FR01: Requirement {i}" for i in range(10)])
            + "\nThe system shall validate all inputs within 200ms."
        )
        rate = _compute_ambiguity_rate(content)
        assert rate == 0.0

    def test_ambiguity_rate_zero_for_no_requirements(self) -> None:
        content = "This is a document with no FRs, no NFRs, no checkboxes."
        rate = _compute_ambiguity_rate(content)
        assert rate == 0.0

    def test_ambiguity_rate_wired_into_v2_result(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD)
        assert isinstance(result.ambiguity_rate, float)
        assert result.ambiguity_rate >= 0.0

    def test_ambiguity_rate_nonzero_for_vague_prd(self) -> None:
        vague_content = (
            _MINIMAL_FRONTMATTER
            + """\
## 4. Functional Requirements

### FR01: Do Something
The system might do this, or possibly that. As needed.
Also should consider alternatives.

### FR02: Other Thing
This might work approximately right.
"""
        )
        result = validate_prd_quality_v2(vague_content)
        assert result.ambiguity_rate > 0.0


class TestRiskProfileWeights:
    """Test that RISK_PROFILES have weight tuples summing to 100."""

    def test_all_profiles_have_4_weights(self) -> None:
        for name, profile in RISK_PROFILES.items():
            assert len(profile.weights) == 4, f"Profile '{name}' has {len(profile.weights)} weights, expected 4"

    def test_all_profiles_weights_sum_to_100(self) -> None:
        for name, profile in RISK_PROFILES.items():
            total = sum(profile.weights)
            assert total == 100, f"Profile '{name}' weights sum to {total}, expected 100"

    def test_risk_scaled_validation_no_stubs(self) -> None:
        result = validate_prd_quality_v2(_FILLED_PRD, risk_level="critical")
        for dim in result.dimensions:
            assert dim.max_score > 0.0, f"Dimension '{dim.name}' has max_score=0.0 after risk scaling"


class TestImprovementSuggestions:
    """Test generate_improvement_suggestions."""

    def test_skeleton_gets_suggestions(self) -> None:
        dims = [
            DimensionScore(name="content_density", score=2.0, max_score=20.0),
            DimensionScore(name="structural_completeness", score=3.0, max_score=20.0),
            DimensionScore(name="implementation_readiness", score=2.0, max_score=25.0),
            DimensionScore(name="traceability", score=0.0, max_score=35.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert len(suggestions) >= 4

    def test_improvement_suggestions_exclude_stubs(self) -> None:
        stub_names = {"smell_score", "readability", "ears_coverage"}
        dims = [
            DimensionScore(name="content_density", score=0.0, max_score=20.0),
            DimensionScore(name="structural_completeness", score=0.0, max_score=20.0),
            DimensionScore(name="implementation_readiness", score=0.0, max_score=25.0),
            DimensionScore(name="traceability", score=0.0, max_score=35.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        for suggestion in suggestions:
            assert suggestion.dimension not in stub_names

    def test_readiness_suggestions_sort_ahead_of_density(self) -> None:
        dims = [
            DimensionScore(name="content_density", score=8.0, max_score=20.0),
            DimensionScore(name="implementation_readiness", score=8.0, max_score=25.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert suggestions[0].dimension == "implementation_readiness"

    def test_max_5_suggestions(self) -> None:
        dims = [DimensionScore(name=f"dim_{i}", score=0.0, max_score=20.0) for i in range(8)]
        suggestions = generate_improvement_suggestions(dims, max_suggestions=5)
        assert len(suggestions) <= 5

    def test_high_scoring_no_suggestions(self) -> None:
        dims = [
            DimensionScore(name="a", score=20.0, max_score=25.0),
            DimensionScore(name="b", score=14.0, max_score=15.0),
        ]
        suggestions = generate_improvement_suggestions(dims)
        assert len(suggestions) == 0
