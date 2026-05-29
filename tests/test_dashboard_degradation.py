"""Degradation-detection tests for quality dashboard."""

from __future__ import annotations

import pytest

from trw_mcp.state.dashboard import detect_degradation


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
            {"ceremony_score": 80},
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
            [{"ceremony_score": 5}],
            threshold=40,
            consecutive=-1,
        )
        assert alerts == []

    def test_critical_severity_for_long_streak(self) -> None:
        """Severity escalates to critical when streak >= 2x consecutive."""
        sessions = [{"ceremony_score": 10} for _ in range(6)]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"


@pytest.mark.unit
class TestDegradationEdgeCases:
    def test_none_score_resets_and_flushes_streak(self) -> None:
        """None ceremony_score resets streak but flushes if >= consecutive."""
        sessions = [
            {"ceremony_score": 10},
            {"ceremony_score": 20},
            {"ceremony_score": 15},
            {"ceremony_score": None},
            {"ceremony_score": 5},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        assert alerts[0]["length"] == 3

    def test_invalid_float_resets_streak(self) -> None:
        """Non-numeric ceremony_score resets the streak."""
        sessions = [
            {"ceremony_score": 10},
            {"ceremony_score": 20},
            {"ceremony_score": "bad"},
            {"ceremony_score": 15},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 0

    def test_multiple_separate_streaks(self) -> None:
        """Multiple separate degradation streaks each produce an alert."""
        sessions = [
            {"ceremony_score": 10},
            {"ceremony_score": 20},
            {"ceremony_score": 15},
            {"ceremony_score": 80},
            {"ceremony_score": 5},
            {"ceremony_score": 10},
            {"ceremony_score": 15},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 2

    def test_first_occurrence_from_timestamp(self) -> None:
        """first_occurrence is extracted from session's timestamp field."""
        sessions = [
            {"ceremony_score": 10, "timestamp": "2026-03-01T00:00:00Z"},
            {"ceremony_score": 20, "timestamp": "2026-03-02T00:00:00Z"},
            {"ceremony_score": 15, "timestamp": "2026-03-03T00:00:00Z"},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        assert alerts[0]["first_occurrence"] == "2026-03-01T00:00:00Z"

    def test_first_occurrence_from_ts_field(self) -> None:
        """first_occurrence falls back to 'ts' field."""
        sessions = [
            {"ceremony_score": 10, "ts": "2026-04-01T00:00:00Z"},
            {"ceremony_score": 20},
            {"ceremony_score": 15},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 1
        assert alerts[0]["first_occurrence"] == "2026-04-01T00:00:00Z"

    def test_streak_exactly_at_boundary(self) -> None:
        """Score exactly equal to threshold does NOT count as degraded."""
        sessions = [
            {"ceremony_score": 40},
            {"ceremony_score": 39},
            {"ceremony_score": 38},
        ]
        alerts = detect_degradation(sessions, threshold=40, consecutive=3)
        assert len(alerts) == 0
