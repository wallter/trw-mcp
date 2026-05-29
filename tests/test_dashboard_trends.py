"""Trend and linear-slope tests for quality dashboard."""

from __future__ import annotations

import pytest

from trw_mcp.state.dashboard import (
    _linear_slope,
    compute_ceremony_trend,
    compute_coverage_trend,
    compute_review_trend,
)


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
        assert result["slope"] is not None
        assert result["pass_rate"] == pytest.approx(1.0, abs=0.01)

    def test_empty_sessions(self) -> None:
        result = compute_ceremony_trend([])
        assert result["session_count"] == 0
        assert result["avg"] is None
        assert result["slope"] is None

    def test_single_session(self) -> None:
        result = compute_ceremony_trend([{"ceremony_score": 50}])
        assert result["session_count"] == 1
        assert result["slope"] is None

    def test_missing_ceremony_score(self) -> None:
        """Sessions without ceremony_score are skipped."""
        sessions = [
            {"ceremony_score": 70},
            {"some_other_field": True},
            {"ceremony_score": 90},
        ]
        result = compute_ceremony_trend(sessions)
        assert result["session_count"] == 2


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
        assert result["below_threshold_count"] == 1
        assert result["min"] == 70.0
        assert result["max"] == 92.0

    def test_empty(self) -> None:
        result = compute_coverage_trend([])
        assert result["session_count"] == 0
        assert result["avg"] is None


@pytest.mark.unit
class TestReviewTrend:
    def test_counts(self) -> None:
        sessions = [
            {"review_verdict": "pass"},
            {"review_verdict": "warn"},
            {"review_verdict": "block"},
            {"review_verdict": "pass"},
            {},
        ]
        result = compute_review_trend(sessions)
        assert result["pass"] == 2
        assert result["warn"] == 1
        assert result["block"] == 1
        assert result["total"] == 4


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


@pytest.mark.unit
class TestCeremonyTrendEdgeCases:
    def test_non_numeric_ceremony_score_skipped(self) -> None:
        """Non-numeric ceremony_score values are skipped."""
        sessions = [
            {"ceremony_score": "not-a-number"},
            {"ceremony_score": 80},
            {"ceremony_score": None},
        ]
        result = compute_ceremony_trend(sessions)
        assert result["session_count"] == 1
        assert result["avg"] == 80.0

    def test_two_sessions_slope_is_none(self) -> None:
        """Two sessions produce a None slope (< 3 points)."""
        sessions = [{"ceremony_score": 50}, {"ceremony_score": 70}]
        result = compute_ceremony_trend(sessions)
        assert result["session_count"] == 2
        assert result["slope"] is None


@pytest.mark.unit
class TestCoverageTrendEdgeCases:
    def test_non_numeric_coverage_skipped(self) -> None:
        """Non-numeric coverage_pct values are skipped."""
        sessions = [
            {"coverage_pct": "bad"},
            {"coverage_pct": 90.0},
        ]
        result = compute_coverage_trend(sessions)
        assert result["session_count"] == 1
        assert result["avg"] == 90.0

    def test_single_session_coverage(self) -> None:
        """Single session produces valid avg/min/max."""
        sessions = [{"coverage_pct": 75.0}]
        result = compute_coverage_trend(sessions)
        assert result["session_count"] == 1
        assert result["avg"] == 75.0
        assert result["min"] == 75.0
        assert result["max"] == 75.0


@pytest.mark.unit
class TestReviewTrendEdgeCases:
    def test_unknown_verdict_ignored(self) -> None:
        """Verdicts not in {block, warn, pass} are ignored."""
        sessions = [
            {"review_verdict": "pass"},
            {"review_verdict": "unknown"},
            {"review_verdict": "PASS"},
        ]
        result = compute_review_trend(sessions)
        assert result["pass"] == 2
        assert result["total"] == 2

    def test_all_empty_sessions(self) -> None:
        """Sessions with no review_verdict produce zero counts."""
        sessions: list[dict[str, object]] = [{}, {}, {}]
        result = compute_review_trend(sessions)
        assert result["total"] == 0


@pytest.mark.unit
class TestLinearSlopeEdgeCases:
    def test_empty_list(self) -> None:
        """Empty input returns None."""
        assert _linear_slope([]) is None

    def test_single_value(self) -> None:
        """Single value returns None."""
        assert _linear_slope([42.0]) is None

    def test_downward_slope(self) -> None:
        """Decreasing values produce negative slope."""
        slope = _linear_slope([30.0, 20.0, 10.0])
        assert slope is not None
        assert slope < 0

    def test_constant_values_zero_slope(self) -> None:
        """Identical values produce zero slope."""
        slope = _linear_slope([7.0, 7.0, 7.0, 7.0])
        assert slope == 0.0
