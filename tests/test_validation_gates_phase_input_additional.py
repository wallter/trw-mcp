"""Additional coverage tests for validation phase input gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._validation_gates_support import _make_run_dir
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import check_phase_input


class TestCheckPhaseInputWithPrdScope:
    """check_phase_input with prd_scope wires through _check_prd_enforcement."""

    def test_implement_with_prd_scope_no_prds_dir(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        (run_dir / "reports" / "plan.md").write_text("# Plan\n", encoding="utf-8")
        writer.write_yaml(run_dir / "shards" / "manifest.yaml", {"waves": []})
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-test1234",
                "task": "coverage-test",
                "status": "active",
                "phase": "implement",
                "prd_scope": ["PRD-MISSING-001"],
            },
        )
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        config = TRWConfig(phase_gate_enforcement="lenient")
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "prd_exists" in rules


class TestPhaseInputStrictSeverity:
    """strict_input_criteria=True escalates failures to error severity."""

    def test_plan_input_strict_makes_failures_errors(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        synthesis_f = [f for f in result.failures if f.rule == "research_complete"]
        assert len(synthesis_f) == 1
        assert synthesis_f[0].severity == "error"
        assert result.valid is False

    def test_plan_input_lenient_makes_failures_warnings(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=False)
        result = check_phase_input(Phase.PLAN, run_dir, config)
        synthesis_f = [f for f in result.failures if f.rule == "research_complete"]
        assert len(synthesis_f) == 1
        assert synthesis_f[0].severity == "warning"
        assert result.valid is True

    def test_implement_input_strict_plan_missing_is_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(
            strict_input_criteria=True,
            phase_gate_enforcement="off",
        )
        result = check_phase_input(Phase.IMPLEMENT, run_dir, config)
        plan_f = [f for f in result.failures if f.rule == "plan_exists"]
        assert len(plan_f) == 1
        assert plan_f[0].severity == "error"

    def test_deliver_input_strict_no_events_is_error(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.DELIVER, run_dir, config)
        events_f = [f for f in result.failures if f.rule == "events_exist"]
        assert len(events_f) == 1
        assert events_f[0].severity == "error"
        assert result.valid is False


class TestValidateInputOSError:
    """_check_validate_input handles OSError from shards iterdir gracefully."""

    def test_validate_input_oserror_treated_as_empty(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        shards = run_dir / "shards"
        shards.mkdir(parents=True, exist_ok=True)

        original_iterdir = Path.iterdir

        def _raise_oserror(self: Path) -> None:
            if "shards" in str(self):
                raise OSError("permission denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raise_oserror)
        config = TRWConfig(strict_input_criteria=True)
        result = check_phase_input(Phase.VALIDATE, run_dir, config)
        rules = [f.rule for f in result.failures]
        assert "implementation_complete" in rules


class TestPhaseInputCompletenessScore:
    """Completeness score reflects the ratio of failures to criteria."""

    def test_research_input_with_run_yaml_has_full_completeness(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_input(Phase.RESEARCH, run_dir, config)
        assert result.completeness_score == 1.0

    def test_implement_input_all_missing_has_low_completeness(
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
        assert result.completeness_score < 1.0
        assert len(result.failures) >= 2


class TestPhaseCriteriaDictCoverage:
    """Ensure all Phase enum values have entries in criteria dicts."""

    def test_all_phases_have_exit_criteria(self) -> None:
        from trw_mcp.state.validation import PHASE_EXIT_CRITERIA

        for phase in Phase:
            assert phase.value in PHASE_EXIT_CRITERIA, f"Phase '{phase.value}' missing from PHASE_EXIT_CRITERIA"

    def test_all_phases_have_input_criteria(self) -> None:
        from trw_mcp.state.validation import PHASE_INPUT_CRITERIA

        for phase in Phase:
            assert phase.value in PHASE_INPUT_CRITERIA, f"Phase '{phase.value}' missing from PHASE_INPUT_CRITERIA"

    def test_exit_criteria_values_are_nonempty_lists(self) -> None:
        from trw_mcp.state.validation import PHASE_EXIT_CRITERIA

        for phase_name, criteria in PHASE_EXIT_CRITERIA.items():
            assert isinstance(criteria, list)
            assert len(criteria) > 0, f"{phase_name} has empty exit criteria"

    def test_input_criteria_values_are_nonempty_lists(self) -> None:
        from trw_mcp.state.validation import PHASE_INPUT_CRITERIA

        for phase_name, criteria in PHASE_INPUT_CRITERIA.items():
            assert isinstance(criteria, list)
            assert len(criteria) > 0, f"{phase_name} has empty input criteria"
