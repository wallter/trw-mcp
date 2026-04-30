"""Tests for validation v2 structure and base traceability behavior."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import score_structural_completeness, score_traceability_v2

from ._validation_v2_support import (
    _FILLED_PRD,
    _PARTIAL_PRD,
    extract_all_12_section_names,
)


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
        assert result.score >= 20.0

    def test_6_sections_half_score(self) -> None:
        sections = [
            "Problem Statement",
            "Goals & Non-Goals",
            "User Stories",
            "Functional Requirements",
            "Non-Functional Requirements",
            "Technical Approach",
        ]
        frontmatter = {"id": "X", "title": "Y", "version": "1.0", "status": "draft", "priority": "P1"}
        result = score_structural_completeness(frontmatter, sections)
        assert result.score < 20.0

    def test_missing_confidence_reduces_score(self) -> None:
        sections = extract_all_12_section_names()
        with_conf = score_structural_completeness(
            {
                "id": "X",
                "title": "Y",
                "version": "1.0",
                "status": "d",
                "priority": "P1",
                "confidence": {
                    "implementation_feasibility": 0.8,
                    "requirement_clarity": 0.8,
                    "estimate_confidence": 0.7,
                },
            },
            sections,
        )
        without_conf = score_structural_completeness(
            {"id": "X", "title": "Y", "version": "1.0", "status": "d", "priority": "P1"},
            sections,
        )
        assert with_conf.score > without_conf.score


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
        assert result.score >= 13.0

    def test_no_traces_zero_score(self) -> None:
        frontmatter: dict[str, object] = {"traceability": {"implements": [], "depends_on": [], "enables": []}}
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
        assert 0.0 < result.score < 33.0


class TestDimensionWeights:
    """Test that active dimension weights sum to 100 and stub weights are 0."""

    def test_active_weights_sum_100(self) -> None:
        config = TRWConfig()
        total = (
            config.validation_density_weight
            + config.validation_structure_weight
            + config.validation_implementation_readiness_weight
            + config.validation_traceability_weight
        )
        assert total == 100.0

    def test_stub_weight_defaults_are_zero(self) -> None:
        config = TRWConfig()
        assert config.validation_smell_weight == 0.0
        assert config.validation_readability_weight == 0.0
        assert config.validation_ears_weight == 0.0

    def test_active_weights_values(self) -> None:
        config = TRWConfig()
        assert config.validation_density_weight == 20.0
        assert config.validation_structure_weight == 20.0
        assert config.validation_implementation_readiness_weight == 25.0
        assert config.validation_traceability_weight == 35.0
