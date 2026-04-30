"""Coverage tests for phase validation result helpers."""

from __future__ import annotations

from pathlib import Path

from tests._validation_gates_support import _make_run_dir
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.models.run import Phase
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.validation import (
    _build_phase_result,
    check_phase_exit,
)


class TestBuildPhaseResult:
    """Boundary conditions for _build_phase_result."""

    def test_no_failures_yields_valid_and_completeness_one(self) -> None:
        result = _build_phase_result(
            failures=[],
            criteria=["crit1", "crit2", "crit3"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is True
        assert result.completeness_score == 1.0
        assert result.failures == []

    def test_warnings_only_still_valid(self) -> None:
        warning = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="warning",
        )
        result = _build_phase_result(
            failures=[warning],
            criteria=["crit1", "crit2"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is True
        assert result.completeness_score == 0.5

    def test_error_makes_result_invalid(self) -> None:
        error = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="error",
        )
        result = _build_phase_result(
            failures=[error],
            criteria=["crit1"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is False

    def test_more_failures_than_criteria_clamps_score_at_zero(self) -> None:
        failures = [ValidationFailure(field="f", rule="r", message="m", severity="warning") for _ in range(5)]
        result = _build_phase_result(
            failures=failures,
            criteria=["crit1"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.completeness_score == 0.0

    def test_empty_criteria_does_not_divide_by_zero(self) -> None:
        warning = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="warning",
        )
        result = _build_phase_result(
            failures=[warning],
            criteria=[],
            phase_name="test",
            log_event="test_event",
        )
        assert result.completeness_score == 0.0

    def test_info_severity_does_not_make_invalid(self) -> None:
        info = ValidationFailure(
            field="f",
            rule="r",
            message="m",
            severity="info",
        )
        result = _build_phase_result(
            failures=[info],
            criteria=["crit1", "crit2"],
            phase_name="test",
            log_event="test_event",
        )
        assert result.valid is True


class TestUnknownPhaseHandling:
    """Phases without a registered checker return empty results gracefully."""

    def test_exit_for_research_with_no_checker_side_effects(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        run_dir = _make_run_dir(tmp_path, writer)
        config = TRWConfig()
        result = check_phase_exit(Phase.RESEARCH, run_dir, config)
        assert isinstance(result.valid, bool)
        assert isinstance(result.completeness_score, float)
