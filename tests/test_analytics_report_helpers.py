"""Tests for analytics report helper functions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tests._test_analytics_report_support import (
    _add_checkpoint,
    _add_events,
    _make_run_id_hours_ago,
    _reader,
)
from trw_mcp.state.analytics.report import (
    _get_last_activity_timestamp,
    _write_archive_summary,
)


class TestGetLastActivityTimestamp:
    """_get_last_activity_timestamp reads checkpoint timestamps."""

    def test_no_checkpoints_file(self, tmp_path: Path) -> None:
        """Returns None when checkpoints.jsonl does not exist."""
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        assert _get_last_activity_timestamp(run_dir) is None

    def test_empty_checkpoints_file(self, tmp_path: Path) -> None:
        """Returns None when checkpoints.jsonl is empty."""
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "checkpoints.jsonl").write_text("", encoding="utf-8")
        assert _get_last_activity_timestamp(run_dir) is None

    def test_single_checkpoint(self, tmp_path: Path) -> None:
        """Returns the timestamp of the single checkpoint."""
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        _add_checkpoint(run_dir, hours_ago=10)

        result = _get_last_activity_timestamp(run_dir)
        assert result is not None
        age = (datetime.now(timezone.utc) - result).total_seconds() / 3600
        assert 9.5 < age < 10.5

    def test_multiple_checkpoints_returns_latest(self, tmp_path: Path) -> None:
        """Returns the most recent checkpoint timestamp."""
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        _add_checkpoint(run_dir, hours_ago=50)
        _add_checkpoint(run_dir, hours_ago=30)
        _add_checkpoint(run_dir, hours_ago=5)

        result = _get_last_activity_timestamp(run_dir)
        assert result is not None
        age = (datetime.now(timezone.utc) - result).total_seconds() / 3600
        assert 4.5 < age < 5.5


class TestWriteArchiveSummary:
    """_write_archive_summary creates a summary.yaml with correct content."""

    def test_creates_summary(self, tmp_path: Path) -> None:
        """summary.yaml is created with all expected fields."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        run_id = _make_run_id_hours_ago(72)
        data: dict[str, object] = {
            "run_id": run_id,
            "task": "my-task",
            "phase": "research",
        }
        now = datetime.now(timezone.utc)
        closed_at_str = now.isoformat()
        _write_archive_summary(run_dir, data, closed_at_str)

        summary_path = meta / "summary.yaml"
        assert summary_path.exists()

        summary = _reader.read_yaml(summary_path)
        assert summary["run_id"] == run_id
        assert summary["task"] == "my-task"
        assert "Stale timeout" in str(summary["reason"]) or summary["reason"] == "stale_timeout"
        assert summary["events_count"] == 0
        assert summary["checkpoints_count"] == 0
        assert str(summary["closed_at"]) == closed_at_str

    def test_counts_events_and_checkpoints(self, tmp_path: Path) -> None:
        """Event and checkpoint counts are correct in summary."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        _add_events(run_dir, count=7)
        _add_checkpoint(run_dir, hours_ago=10)
        _add_checkpoint(run_dir, hours_ago=5)
        _add_checkpoint(run_dir, hours_ago=1)

        run_id = _make_run_id_hours_ago(72)
        data: dict[str, object] = {"run_id": run_id, "task": "t"}
        _write_archive_summary(run_dir, data, datetime.now(timezone.utc).isoformat())

        summary = _reader.read_yaml(meta / "summary.yaml")
        assert summary["events_count"] == 7
        assert summary["checkpoints_count"] == 3
