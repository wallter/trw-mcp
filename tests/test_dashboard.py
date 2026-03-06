"""Tests for quality dashboard — PRD-QUAL-031.

Covers: ceremony trend, coverage trend, review trend, degradation detection,
sprint comparison, backward compat, empty sessions, single session.
"""

from __future__ import annotations

import pytest

from trw_mcp.state.dashboard import (
    _linear_slope,
    aggregate_dashboard,
    compare_sprints,
    compute_ceremony_trend,
    compute_coverage_trend,
    compute_review_trend,
    detect_degradation,
)


# ---------------------------------------------------------------------------
# Ceremony trend
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCeremonyTrend:
    def test_three_sessions(self) -> None:
        sessions = [
            {"ceremony_score": 80},
            {"ceremony_score": 60},
            {"ceremony_score": 90},
        ]
        result = compute_ceremony_trend(sessions)
        assert result["session_count"] == 3
        assert result["avg"] == pytest.approx(76.67, abs=0.01)
        assert result["min"] == 60.0
        assert result["max"] == 90.0
        assert result["slope"] is not None  # 3+ points
        # pass_rate uses ceremony_alert_threshold from config (default 40)
        # All three scores (80, 60, 90) are >= 40, so pass_rate = 1.0
        assert result["pass_rate"] == pytest.approx(1.0, abs=0.01)

    def test_empty_sessions(self) -> None:
        result = compute_ceremony_trend([])
        assert result["session_count"] == 0
        assert result["avg"] is None
        assert result["slope"] is None

    def test_single_session(self) -> None:
        result = compute_ceremony_trend([{"ceremony_score": 50}])
        assert result["session_count"] == 1
        assert result["slope"] is None  # < 3 points

    def test_missing_ceremony_score(self) -> None:
        """Sessions without ceremony_score are skipped."""
        sessions = [
            {"ceremony_score": 70},
            {"some_other_field": True},
            {"ceremony_score": 90},
        ]
        result = compute_ceremony_trend(sessions)
        assert result["session_count"] == 2


# ---------------------------------------------------------------------------
# Coverage trend
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoverageTrend:
    def test_basic(self) -> None:
        sessions = [
            {"coverage_pct": 85.0},
            {"coverage_pct": 70.0},
            {"coverage_pct": 92.0},
        ]
        result = compute_coverage_trend(sessions)
        assert result["session_count"] == 3
        assert result["below_threshold_count"] == 1  # 70 < 80
        assert result["min"] == 70.0
        assert result["max"] == 92.0

    def test_empty(self) -> None:
        result = compute_coverage_trend([])
        assert result["session_count"] == 0
        assert result["avg"] is None


# ---------------------------------------------------------------------------
# Review trend
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReviewTrend:
    def test_counts(self) -> None:
        sessions = [
            {"review_verdict": "pass"},
            {"review_verdict": "warn"},
            {"review_verdict": "block"},
            {"review_verdict": "pass"},
            {},  # missing verdict
        ]
        result = compute_review_trend(sessions)
        assert result["pass"] == 2
        assert result["warn"] == 1
        assert result["block"] == 1
        assert result["total"] == 4


# ---------------------------------------------------------------------------
# Degradation detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDegradation:
    def test_three_consecutive_below(self) -> None:
        sessions = [
            {"ceremony_score": 30},
            {"ceremony_score": 20},
            {"ceremony_score": 35},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["length"] == 3
        assert alert["type"] == "ceremony_degradation"
        assert alert["consecutive_sessions"] == 3
        assert alert["threshold"] == 40
        assert alert["severity"] == "warning"
        assert "first_occurrence" in alert

    def test_intermittent_no_alert(self) -> None:
        """Intermittent low scores don't trigger consecutive alert."""
        sessions = [
            {"ceremony_score": 30},
            {"ceremony_score": 80},  # breaks streak
            {"ceremony_score": 20},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 0

    def test_no_degradation(self) -> None:
        sessions = [
            {"ceremony_score": 80},
            {"ceremony_score": 90},
            {"ceremony_score": 85},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 0

    def test_longer_streak(self) -> None:
        sessions = [
            {"ceremony_score": 10},
            {"ceremony_score": 20},
            {"ceremony_score": 15},
            {"ceremony_score": 25},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        assert alerts[0]["length"] == 4
        assert alerts[0]["type"] == "ceremony_degradation"
        assert alerts[0]["severity"] == "warning"

    def test_consecutive_zero_disables_alerting(self) -> None:
        """consecutive=0 should return empty list (disable alerting)."""
        sessions = [
            {"ceremony_score": 10},
            {"ceremony_score": 20},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=0)
        assert alerts == []

    def test_consecutive_negative_disables_alerting(self) -> None:
        """Negative consecutive also returns empty."""
        alerts = detect_degradation(
            [{"ceremony_score": 5}], threshold=40, consecutive=-1,
        )
        assert alerts == []

    def test_critical_severity_for_long_streak(self) -> None:
        """Severity escalates to critical when streak >= 2x consecutive."""
        sessions = [{"ceremony_score": 10} for _ in range(6)]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"


# ---------------------------------------------------------------------------
# Sprint comparison
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Linear slope
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLinearSlope:
    def test_two_points(self) -> None:
        assert _linear_slope([1.0, 2.0]) is None

    def test_three_points_upward(self) -> None:
        slope = _linear_slope([10.0, 20.0, 30.0])
        assert slope is not None
        assert slope == pytest.approx(10.0, abs=0.01)

    def test_flat(self) -> None:
        slope = _linear_slope([5.0, 5.0, 5.0])
        assert slope == 0.0


# ---------------------------------------------------------------------------
# Backward compat (missing fields)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackwardCompat:
    def test_sessions_with_missing_fields(self) -> None:
        """Sessions with no recognized fields produce empty trends gracefully."""
        sessions: list[dict[str, object]] = [
            {"unknown_field": "value"},
            {"another": 123},
        ]
        ceremony = compute_ceremony_trend(sessions)
        assert ceremony["session_count"] == 0

        coverage = compute_coverage_trend(sessions)
        assert coverage["session_count"] == 0

        review = compute_review_trend(sessions)
        assert review["total"] == 0


# ---------------------------------------------------------------------------
# Aggregate dashboard (integration)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAggregateDashboard:
    def test_with_empty_trw_dir(self, tmp_path: object) -> None:
        """aggregate_dashboard handles missing files gracefully."""
        from pathlib import Path

        trw_dir = Path(str(tmp_path)) / ".trw"
        trw_dir.mkdir(parents=True)
        result = aggregate_dashboard(trw_dir, window_days=30)
        assert "ceremony_trend" in result
        assert "coverage_trend" in result
        assert "review_trend" in result
        assert "alerts" in result
        assert result["legacy_runs_skipped"] == 0
