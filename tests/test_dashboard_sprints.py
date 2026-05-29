"""Sprint comparison and sprint-id tests for quality dashboard."""

from __future__ import annotations

import pytest

from trw_mcp.state.dashboard import _sprint_id, compare_sprints


@pytest.mark.unit
class TestSprintComparison:
    def test_basic_comparison(self) -> None:
        sessions = [
            {"task_name": "sprint-46-feature", "ceremony_score": 60, "coverage_pct": 80},
            {"task_name": "sprint-46-bugfix", "ceremony_score": 70, "coverage_pct": 85},
            {"task_name": "sprint-47-feature", "ceremony_score": 80, "coverage_pct": 90},
            {"task_name": "sprint-47-other", "ceremony_score": 90, "coverage_pct": 95},
        ]
        result = compare_sprints(sessions, "sprint-46-feature", "sprint-47-feature")
        assert result is not None
        assert result["sprint_a"] == "sprint-46-feature"
        assert result["sprint_b"] == "sprint-47-feature"

    def test_missing_sprint(self) -> None:
        sessions = [
            {"task_name": "sprint-46-feature", "ceremony_score": 60},
        ]
        result = compare_sprints(sessions, "sprint-46-feature", "sprint-99-missing")
        assert result is None


@pytest.mark.unit
class TestSprintId:
    def test_sprint_dash_pattern(self) -> None:
        """task_name with 'sprint-NN' extracts the sprint prefix."""
        result = _sprint_id({"task_name": "sprint-42-bugfix"})
        assert result == "sprint-42-bugfix"

    def test_sprint_underscore_pattern(self) -> None:
        """task_name with 'sprint_NN' extracts the sprint prefix."""
        result = _sprint_id({"task_name": "do sprint_42 work"})
        assert result.startswith("sprint_42")

    def test_sprint_space_pattern(self) -> None:
        """task_name with 'sprint N' extracts the sprint token."""
        result = _sprint_id({"task_name": "run sprint 42 delivery"})
        assert "sprint" in result.lower()

    def test_no_sprint_pattern(self) -> None:
        """task_name without sprint pattern returns the full task_name."""
        result = _sprint_id({"task_name": "fix-auth-bug"})
        assert result == "fix-auth-bug"

    def test_empty_task_name(self) -> None:
        """Empty task_name returns 'untagged'."""
        result = _sprint_id({"task_name": ""})
        assert result == "untagged"

    def test_missing_task_name(self) -> None:
        """Missing task_name returns 'untagged'."""
        result = _sprint_id({})
        assert result == "untagged"


@pytest.mark.unit
class TestSprintComparisonEdgeCases:
    def test_both_sprints_missing(self) -> None:
        """Neither sprint found returns None."""
        result = compare_sprints([], "sprint-a", "sprint-b")
        assert result is None

    def test_one_sprint_missing(self) -> None:
        """Only one sprint found returns None."""
        sessions = [{"task_name": "sprint-1-feat", "ceremony_score": 80}]
        result = compare_sprints(sessions, "sprint-1-feat", "sprint-99")
        assert result is None

    def test_delta_with_none_values(self) -> None:
        """Deltas return None when one side has no data for that metric."""
        sessions = [
            {"task_name": "sprint-a", "ceremony_score": 60},
            {"task_name": "sprint-b", "ceremony_score": 80, "coverage_pct": 90},
        ]
        result = compare_sprints(sessions, "sprint-a", "sprint-b")
        assert result is not None
        assert result["ceremony_avg_delta"] == pytest.approx(20.0, abs=0.01)
        assert result["coverage_avg_delta"] is None
