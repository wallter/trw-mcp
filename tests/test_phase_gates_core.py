"""Integration tests for core phase gate constants and helpers."""

from __future__ import annotations

import pytest

from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation.phase_gates import (
    PHASE_EXIT_CRITERIA,
    PHASE_INPUT_CRITERIA,
    _build_phase_result,
)


class TestPhaseConstants:
    """Verify PHASE_INPUT_CRITERIA and PHASE_EXIT_CRITERIA are sane."""

    @pytest.mark.unit
    def test_input_criteria_has_all_phases(self) -> None:
        for phase in ("research", "plan", "implement", "validate", "review", "deliver"):
            assert phase in PHASE_INPUT_CRITERIA, f"{phase} missing from PHASE_INPUT_CRITERIA"

    @pytest.mark.unit
    def test_exit_criteria_has_all_phases(self) -> None:
        for phase in ("research", "plan", "implement", "validate", "review", "deliver"):
            assert phase in PHASE_EXIT_CRITERIA, f"{phase} missing from PHASE_EXIT_CRITERIA"

    @pytest.mark.unit
    def test_input_criteria_nonempty(self) -> None:
        for phase, criteria in PHASE_INPUT_CRITERIA.items():
            assert len(criteria) > 0, f"{phase} has empty input criteria"

    @pytest.mark.unit
    def test_exit_criteria_nonempty(self) -> None:
        for phase, criteria in PHASE_EXIT_CRITERIA.items():
            assert len(criteria) > 0, f"{phase} has empty exit criteria"


class TestBuildPhaseResult:
    """Unit tests for _build_phase_result helper."""

    @pytest.mark.unit
    def test_no_failures_is_valid(self) -> None:
        result = _build_phase_result([], ["crit1", "crit2"], "research", "phase_exit_checked")
        assert result.valid is True
        assert result.completeness_score == 1.0

    @pytest.mark.unit
    def test_error_severity_marks_invalid(self) -> None:
        failures = [ValidationFailure(field="f", rule="r", message="m", severity="error")]
        result = _build_phase_result(failures, ["crit1"], "plan", "phase_exit_checked")
        assert result.valid is False

    @pytest.mark.unit
    def test_warning_only_stays_valid(self) -> None:
        failures = [ValidationFailure(field="f", rule="r", message="m", severity="warning")]
        result = _build_phase_result(failures, ["crit1"], "research", "phase_exit_checked")
        assert result.valid is True

    @pytest.mark.unit
    def test_completeness_score_decreases_with_failures(self) -> None:
        failures = [
            ValidationFailure(field="f", rule="r", message="m", severity="warning"),
            ValidationFailure(field="f2", rule="r2", message="m2", severity="warning"),
        ]
        result = _build_phase_result(failures, ["c1", "c2", "c3", "c4"], "plan", "phase_exit_checked")
        assert result.completeness_score < 1.0
        assert result.completeness_score >= 0.0
