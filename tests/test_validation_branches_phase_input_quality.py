"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_input, validate_prd_quality_v2

from tests._validation_branches_support import _MINIMAL_PRD_CONTENT, _make_run_dir


class TestCheckPhaseInputUncovered:
    """Cover additional branches in check_phase_input."""

    def test_research_phase_missing_run_yaml_meta_dir(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_no_meta"
        run_dir.mkdir()
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        assert result.valid is False
        assert any(f.rule == "run_initialized" for f in result.failures)

    def test_plan_phase_warning_severity_in_lenient_mode(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=False)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        synthesis_failures = [f for f in result.failures if f.rule == "research_complete"]
        assert all(f.severity == "warning" for f in synthesis_failures)
        assert result.valid is True

    def test_implement_warning_severity_in_lenient_mode(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=False,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        plan_failures = [f for f in result.failures if f.rule == "plan_exists"]
        assert all(f.severity == "warning" for f in plan_failures)

    def test_validate_phase_empty_shards_dir_in_strict_mode(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        failures = [f for f in result.failures if f.rule == "implementation_complete"]
        assert len(failures) == 1
        assert failures[0].severity == "error"

    def test_review_phase_with_events_empty_file(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        (meta / "events.jsonl").write_text("", encoding="utf-8")
        config = TRWConfig()
        result = check_phase_input(Phase.REVIEW, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "validate_passed" not in rules

    def test_deliver_phase_events_with_reflection_and_complete(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        meta = run_dir / "meta"
        writer.append_jsonl(
            meta / "events.jsonl",
            {
                "event": "trw_reflect_complete",
                "ts": "2026-01-01T00:00:00Z",
            },
        )
        config = TRWConfig()
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        assert not any(f.rule in ("reflection_complete", "events_exist") for f in result.failures)


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
        config = TRWConfig(
            validation_density_weight=0.0,
            validation_structure_weight=0.0,
            validation_implementation_readiness_weight=0.0,
            validation_traceability_weight=0.0,
            validation_smell_weight=0.0,
            validation_readability_weight=0.0,
            validation_ears_weight=0.0,
            risk_scaling_enabled=False,
        )
        result = validate_prd_quality_v2(_MINIMAL_PRD_CONTENT, config=config)
        assert result.total_score == 0.0
