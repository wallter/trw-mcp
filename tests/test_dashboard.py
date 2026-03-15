"""Tests for quality dashboard — PRD-QUAL-031.

Covers: ceremony trend, coverage trend, review trend, degradation detection,
sprint comparison, backward compat, empty sessions, single session, plus
edge cases for _sprint_id, non-numeric values, streak breaks.
"""

from __future__ import annotations

import json

import pytest

from trw_mcp.state.dashboard import (
    _linear_slope,
    _sprint_id,
    aggregate_dashboard,
    compare_sprints,
    compute_ceremony_trend,
    compute_coverage_trend,
    compute_review_trend,
    detect_degradation,
)
from trw_mcp.state.persistence import FileStateWriter

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

    def test_with_session_events(self, tmp_path: object) -> None:
        """Session events are parsed and fed into trend computations."""
        from datetime import datetime, timezone
        from pathlib import Path

        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        events = [
            {"timestamp": now_iso, "data": {"ceremony_score": 80, "coverage_pct": 90, "review_verdict": "pass"}},
            {"timestamp": now_iso, "data": {"ceremony_score": 60, "coverage_pct": 75, "review_verdict": "warn"}},
            {"timestamp": now_iso, "data": {"ceremony_score": 70, "coverage_pct": 85, "review_verdict": "pass"}},
        ]
        events_path = context_dir / "session-events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30)
        assert result["ceremony_trend"]["session_count"] == 3
        assert result["coverage_trend"]["session_count"] == 3
        assert result["review_trend"]["pass"] == 2
        assert result["review_trend"]["warn"] == 1

    def test_legacy_events_skipped(self, tmp_path: object) -> None:
        """Events with unparseable timestamps increment legacy_skipped."""
        from pathlib import Path

        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        events = [
            {"timestamp": "not-a-date", "data": {"ceremony_score": 50}},
            {"ts": "also-bad", "data": {"ceremony_score": 60}},
        ]
        events_path = context_dir / "session-events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30)
        assert result["legacy_runs_skipped"] == 2

    def test_sprint_comparison_populated(self, tmp_path: object) -> None:
        """Sprint comparison is populated when compare_sprint matches."""
        from datetime import datetime, timezone
        from pathlib import Path

        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        events = [
            {"timestamp": now_iso, "data": {"ceremony_score": 60, "task_name": "sprint-1-feat"}},
            {"timestamp": now_iso, "data": {"ceremony_score": 80, "task_name": "sprint-2-feat"}},
        ]
        events_path = context_dir / "session-events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30, compare_sprint="sprint-2-feat")
        # sprint-2-feat is second, so idx > 0 and comparison is created
        assert result["sprint_comparison"] is not None

    def test_analytics_yaml_loaded(self, tmp_path: object) -> None:
        """analytics.yaml counters are included in metadata."""
        from pathlib import Path

        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(context_dir / "analytics.yaml", {"sessions_total": 42})

        result = aggregate_dashboard(trw_dir, window_days=30)
        meta = result["metadata"]
        assert isinstance(meta, dict)
        counters = meta["analytics_counters"]
        assert isinstance(counters, dict)
        assert counters["sessions_total"] == 42

    def test_events_with_top_level_fields(self, tmp_path: object) -> None:
        """Events where fields are at top level (not nested in 'data') are still extracted."""
        from datetime import datetime, timezone
        from pathlib import Path

        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        events = [
            {"timestamp": now_iso, "ceremony_score": 75, "coverage_pct": 88},
        ]
        events_path = context_dir / "session-events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30)
        assert result["ceremony_trend"]["session_count"] == 1


# ---------------------------------------------------------------------------
# _sprint_id edge cases
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Ceremony trend edge cases
# ---------------------------------------------------------------------------


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
        # Only the 80 is valid; None is filtered by val is not None, "not-a-number" by except
        assert result["session_count"] == 1
        assert result["avg"] == 80.0

    def test_two_sessions_slope_is_none(self) -> None:
        """Two sessions produce a None slope (< 3 points)."""
        sessions = [{"ceremony_score": 50}, {"ceremony_score": 70}]
        result = compute_ceremony_trend(sessions)
        assert result["session_count"] == 2
        assert result["slope"] is None


# ---------------------------------------------------------------------------
# Coverage trend edge cases
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Review trend edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReviewTrendEdgeCases:
    def test_unknown_verdict_ignored(self) -> None:
        """Verdicts not in {block, warn, pass} are ignored."""
        sessions = [
            {"review_verdict": "pass"},
            {"review_verdict": "unknown"},
            {"review_verdict": "PASS"},  # case-insensitive -> pass
        ]
        result = compute_review_trend(sessions)
        assert result["pass"] == 2  # "pass" and "PASS"
        assert result["total"] == 2

    def test_all_empty_sessions(self) -> None:
        """Sessions with no review_verdict produce zero counts."""
        sessions: list[dict[str, object]] = [{}, {}, {}]
        result = compute_review_trend(sessions)
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Degradation detection edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDegradationEdgeCases:
    def test_none_score_resets_and_flushes_streak(self) -> None:
        """None ceremony_score resets streak but flushes if >= consecutive."""
        sessions = [
            {"ceremony_score": 10},
            {"ceremony_score": 20},
            {"ceremony_score": 15},
            {"ceremony_score": None},  # resets, flushes 3-streak
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
            {"ceremony_score": "bad"},  # resets
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
            {"ceremony_score": 80},  # breaks streak
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
        # 40 >= 40 is not degraded, so streak is only 2 (39, 38) -> no alert
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Sprint comparison edge cases
# ---------------------------------------------------------------------------


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
        # Sprint A has ceremony but no coverage, Sprint B has both
        sessions = [
            {"task_name": "sprint-a", "ceremony_score": 60},
            {"task_name": "sprint-b", "ceremony_score": 80, "coverage_pct": 90},
        ]
        result = compare_sprints(sessions, "sprint-a", "sprint-b")
        assert result is not None
        assert result["ceremony_avg_delta"] == pytest.approx(20.0, abs=0.01)
        # Coverage delta should be None since sprint_a has no coverage data
        assert result["coverage_avg_delta"] is None


# ---------------------------------------------------------------------------
# Linear slope edge cases
# ---------------------------------------------------------------------------


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
