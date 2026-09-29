"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from unittest.mock import patch

from tests._validation_branches_support import _MINIMAL_PRD_CONTENT
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import validate_prd_quality_v2


class TestValidatePrdQualityV2ExceptionBranches:
    """Cover exception-handling branches in validate_prd_quality_v2."""

    def test_v1_result_precomputed_skips_v1_computation(self) -> None:
        v1_precomputed: dict[str, object] = {
            "valid": True,
            "failures": [],
            "completeness_score": 0.9,
            "traceability_coverage": 0.8,
        }
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, v1_result=v1_precomputed)
        assert result.valid is True
        assert result.completeness_score == 0.9
        assert result.traceability_coverage == 0.8

    def test_v1_result_with_raw_dict_failures(self) -> None:
        v1_precomputed: dict[str, object] = {
            "valid": False,
            "failures": [{"field": "f1", "rule": "r1", "message": "m1", "severity": "error"}],
            "completeness_score": 0.5,
            "traceability_coverage": 0.0,
        }
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, v1_result=v1_precomputed)
        assert result.valid is False
        assert len(result.failures) == 1
        assert result.failures[0].rule == "r1"

    def test_density_exception_produces_zero_score(self) -> None:
        with patch(
            "trw_mcp.state.validation.prd_quality.score_content_density",
            side_effect=RuntimeError("density error"),
        ):
            result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT)
        density = next(d for d in result.dimensions if d.name == "content_density")
        assert density.score == 0.0

    def test_structure_exception_produces_zero_score(self) -> None:
        with patch(
            "trw_mcp.state.validation.prd_quality.score_structural_completeness",
            side_effect=RuntimeError("structure error"),
        ):
            result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT)
        structure = next(d for d in result.dimensions if d.name == "structural_completeness")
        assert structure.score == 0.0

    def test_traceability_exception_produces_zero_score(self) -> None:
        with patch(
            "trw_mcp.state.validation.prd_quality.score_traceability_v2",
            side_effect=RuntimeError("trace error"),
        ):
            result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT)
        trace = next(d for d in result.dimensions if d.name == "traceability")
        assert trace.score == 0.0

    def test_risk_level_explicit_override_in_v2(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = validate_prd_quality_v2(
            _MINIMAL_PRD_CONTENT,
            config=config,
            risk_level="critical",
        )
        assert result.effective_risk_level == "critical"
        assert result.risk_scaled is True

    def test_all_dimensions_zero_when_max_possible_zero(self) -> None:
        # validation_smell_weight, validation_readability_weight, and
        # validation_ears_weight were removed under PRD-CORE-291 (slice 2):
        # they never affected total_score (documented permanently-0 advisory
        # tunables), so omitting them here changes nothing about this test.
        config = TRWConfig(
            validation_density_weight=0.0,
            validation_structure_weight=0.0,
            validation_implementation_readiness_weight=0.0,
            validation_traceability_weight=0.0,
            risk_scaling_enabled=False,
        )
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, config=config)
        assert result.total_score == 0.0
