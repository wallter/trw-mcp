"""Run report event parsing and learning yield tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.report import compute_learning_yield, parse_run_events


class TestEventParsing:
    """Tests for parse_run_events function."""

    def test_known_events(self) -> None:
        """parse_run_events correctly counts event types."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-19T10:30:00Z", "event": "checkpoint"},
        ]
        summary, timeline, duration, rate = parse_run_events(events)

        assert summary.total_count == 4
        assert summary.by_type["run_init"] == 1
        assert summary.by_type["phase_enter"] == 2
        assert summary.by_type["checkpoint"] == 1
        assert len(timeline) == 2
        assert rate == 0.0

    def test_empty_events(self) -> None:
        """parse_run_events handles empty event list."""
        summary, timeline, duration, rate = parse_run_events([])

        assert summary.total_count == 0
        assert summary.by_type == {}
        assert timeline == []
        assert duration.start_ts is None
        assert rate == 0.0

    def test_malformed_event_types(self) -> None:
        """Events with missing type field counted as 'unknown'."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z"},
            {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
        ]
        summary, _, _, _ = parse_run_events(events)

        assert summary.total_count == 2
        assert summary.by_type.get("unknown") == 1

    def test_phase_timeline_three_transitions(self) -> None:
        """Phase timeline with 3 phase transitions computes durations."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-19T10:30:00Z", "event": "phase_enter", "phase": "implement"},
        ]
        _, timeline, _, _ = parse_run_events(events)

        assert len(timeline) == 3
        assert timeline[0].phase == "research"
        assert timeline[0].duration_seconds == 900.0
        assert timeline[1].phase == "plan"
        assert timeline[1].duration_seconds == 900.0
        assert timeline[2].phase == "implement"
        assert timeline[2].exited_at is None

    def test_single_phase_no_exit(self) -> None:
        """Single phase_enter produces one entry with no exit."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "phase_enter", "phase": "research"},
        ]
        _, timeline, _, _ = parse_run_events(events)

        assert len(timeline) == 1
        assert timeline[0].exited_at is None
        assert timeline[0].duration_seconds is None

    def test_reversion_rate_computed(self) -> None:
        """Reversion rate computed correctly."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "phase_enter", "phase": "research"},
            {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
            {"ts": "2026-02-19T10:20:00Z", "event": "phase_revert", "from": "plan", "to": "research"},
            {"ts": "2026-02-19T10:25:00Z", "event": "phase_enter", "phase": "plan"},
        ]
        _, _, _, rate = parse_run_events(events)

        assert rate == pytest.approx(0.25)

    def test_duration_first_last_event(self) -> None:
        """Duration computed from first and last event timestamps."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-19T13:00:00Z", "event": "checkpoint"},
        ]
        _, _, duration, _ = parse_run_events(events)

        assert duration.start_ts == "2026-02-19T10:00:00Z"
        assert duration.end_ts == "2026-02-19T13:00:00Z"
        assert duration.elapsed_seconds == 10800.0

    def test_event_classification_covers_all_types(self) -> None:
        """All distinct event types get their own count."""
        events: list[dict[str, object]] = [
            {"ts": "2026-02-19T10:00:00Z", "event": "run_init"},
            {"ts": "2026-02-19T10:01:00Z", "event": "tests_passed"},
            {"ts": "2026-02-19T10:02:00Z", "event": "build_passed"},
            {"ts": "2026-02-19T10:03:00Z", "event": "reflection_completed"},
        ]
        summary, _, _, _ = parse_run_events(events)

        assert len(summary.by_type) == 4
        for event_type in ["run_init", "tests_passed", "build_passed", "reflection_completed"]:
            assert summary.by_type[event_type] == 1


class TestLearningYield:
    """Tests for compute_learning_yield function."""

    def test_mixed_impact_learnings(self, tmp_path: Path) -> None:
        """Learning yield computes correct averages with mixed impacts."""
        mock_entries: list[dict[str, object]] = [
            {
                "id": "L-1",
                "summary": "High",
                "detail": "d",
                "impact": 0.9,
                "tags": ["arch"],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-2",
                "summary": "Med",
                "detail": "d",
                "impact": 0.5,
                "tags": ["testing"],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-3",
                "summary": "Low",
                "detail": "d",
                "impact": 0.2,
                "tags": ["arch", "testing"],
                "created": "2026-02-19",
                "status": "active",
            },
        ]
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=mock_entries):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 3
        assert result.avg_impact == pytest.approx(0.533, abs=0.01)
        assert result.high_impact_count == 1
        assert "arch" in result.tags_used
        assert "testing" in result.tags_used

    def test_no_learnings(self, tmp_path: Path) -> None:
        """Learning yield returns zeros when list_active_learnings returns empty."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=[]):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 0
        assert result.avg_impact == 0.0
        assert result.high_impact_count == 0
        assert result.tags_used == []

    def test_sqlite_error_returns_empty(self, tmp_path: Path) -> None:
        """Learning yield returns empty summary when SQLite raises."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch(
            "trw_mcp.state.report.list_active_learnings",
            side_effect=RuntimeError("db error"),
        ):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 0

    def test_date_range_filter(self, tmp_path: Path) -> None:
        """Learning yield filters by date range when provided."""
        mock_entries: list[dict[str, object]] = [
            {
                "id": "L-in",
                "summary": "In range",
                "detail": "d",
                "impact": 0.8,
                "tags": [],
                "created": "2026-02-19",
                "status": "active",
            },
            {
                "id": "L-out",
                "summary": "Out range",
                "detail": "d",
                "impact": 0.8,
                "tags": [],
                "created": "2026-02-10",
                "status": "active",
            },
        ]
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch("trw_mcp.state.report.list_active_learnings", return_value=mock_entries):
            result = compute_learning_yield(
                trw_dir,
                reader,
                run_start="2026-02-19T10:00:00Z",
                run_end="2026-02-19T13:00:00Z",
            )

        assert result.total_produced == 1
