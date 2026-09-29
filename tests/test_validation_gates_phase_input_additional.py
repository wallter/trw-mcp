"""Additional coverage tests for validation phase input gates."""

from __future__ import annotations

from trw_mcp.models.run import Phase


class TestPhaseCriteriaDictCoverage:
    """Ensure all Phase enum values have entries in criteria dicts."""

    def test_all_phases_have_exit_criteria(self) -> None:
        from trw_mcp.state.validation import PHASE_EXIT_CRITERIA

        for phase in Phase:
            assert phase.value in PHASE_EXIT_CRITERIA, f"Phase '{phase.value}' missing from PHASE_EXIT_CRITERIA"

    def test_exit_criteria_values_are_nonempty_lists(self) -> None:
        from trw_mcp.state.validation import PHASE_EXIT_CRITERIA

        for phase_name, criteria in PHASE_EXIT_CRITERIA.items():
            assert isinstance(criteria, list)
            assert len(criteria) > 0, f"{phase_name} has empty exit criteria"
