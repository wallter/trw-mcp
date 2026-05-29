"""Aggregate-dashboard integration tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.state.dashboard import aggregate_dashboard
from trw_mcp.state.persistence import FileStateWriter


@pytest.mark.unit
class TestAggregateDashboard:
    def test_with_empty_trw_dir(self, tmp_path: object) -> None:
        """aggregate_dashboard handles missing files gracefully."""
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
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30)
        assert result["ceremony_trend"]["session_count"] == 3
        assert result["coverage_trend"]["session_count"] == 3
        assert result["review_trend"]["pass"] == 2
        assert result["review_trend"]["warn"] == 1

    def test_legacy_events_skipped(self, tmp_path: object) -> None:
        """Events with unparseable timestamps increment legacy_skipped."""
        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        events = [
            {"timestamp": "not-a-date", "data": {"ceremony_score": 50}},
            {"ts": "also-bad", "data": {"ceremony_score": 60}},
        ]
        events_path = context_dir / "session-events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30)
        assert result["legacy_runs_skipped"] == 2

    def test_sprint_comparison_populated(self, tmp_path: object) -> None:
        """Sprint comparison is populated when compare_sprint matches."""
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
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30, compare_sprint="sprint-2-feat")
        assert result["sprint_comparison"] is not None

    def test_analytics_yaml_loaded(self, tmp_path: object) -> None:
        """analytics.yaml counters are included in metadata."""
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
        trw_dir = Path(str(tmp_path)) / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        events = [
            {"timestamp": now_iso, "ceremony_score": 75, "coverage_pct": 88},
        ]
        events_path = context_dir / "session-events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = aggregate_dashboard(trw_dir, window_days=30)
        assert result["ceremony_trend"]["session_count"] == 1
