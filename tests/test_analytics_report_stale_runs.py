"""Tests for stale-run analytics report behavior."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from tests._test_analytics_report_support import (
    _add_checkpoint,
    _add_events,
    _create_run,
    _make_run_id_hours_ago,
    _patch_config_and_root,
    _reader,
)
from trw_mcp.state.analytics.report import auto_close_stale_runs


class TestStaleRunHourLevelTTL:
    """auto_close_stale_runs detects runs exceeding hour-level TTL."""

    def test_stale_run_hour_level_ttl(self, tmp_path: Path) -> None:
        """A run older than 48h with no checkpoints is closed."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])

        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert updated["status"] == "abandoned"
        assert "abandoned_at" in updated
        assert "abandoned_reason" in updated
        assert "threshold: 48h" in str(updated["abandoned_reason"])
        assert updated.get("original_phase") == "implement"

    def test_stale_run_within_ttl(self, tmp_path: Path) -> None:
        """A run within the TTL window is not closed."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(24)
        _create_run(runs_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0
        assert result["runs_closed"] == []


class TestStaleRunCheckpointExtendsTTL:
    """A recent checkpoint resets the stale clock."""

    def test_stale_run_checkpoint_extends_ttl(self, tmp_path: Path) -> None:
        """An old run with a recent checkpoint is NOT closed."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)
        _add_checkpoint(run_dir, hours_ago=12)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0
        assert result["runs_closed"] == []

        data = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == "active"

    def test_old_checkpoint_does_not_extend_ttl(self, tmp_path: Path) -> None:
        """A run with only old checkpoints is still closed."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(96)
        run_dir = _create_run(runs_root, "test-task", run_id)
        _add_checkpoint(run_dir, hours_ago=72)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])

        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert updated["status"] == "abandoned"

    def test_multiple_checkpoints_uses_latest(self, tmp_path: Path) -> None:
        """When multiple checkpoints exist, the most recent one determines staleness."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(96)
        run_dir = _create_run(runs_root, "test-task", run_id)
        _add_checkpoint(run_dir, hours_ago=72)
        _add_checkpoint(run_dir, hours_ago=6)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0


class TestStaleRunArchiveSummary:
    """auto_close_stale_runs writes summary.yaml when closing stale runs."""

    def test_stale_run_archive_summary(self, tmp_path: Path) -> None:
        """summary.yaml is created with correct fields when a run is closed."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)

        _add_events(run_dir, count=5)
        _add_checkpoint(run_dir, hours_ago=60)
        _add_checkpoint(run_dir, hours_ago=55)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 1

        summary_path = run_dir / "meta" / "summary.yaml"
        assert summary_path.exists(), "summary.yaml should be created"

        summary = _reader.read_yaml(summary_path)
        assert summary["run_id"] == run_id
        assert summary["task"] == "test-task"
        assert "Stale timeout" in str(summary["reason"]) or summary["reason"] == "stale_timeout"
        assert summary["events_count"] == 5
        assert summary["checkpoints_count"] == 2
        assert "closed_at" in summary
        assert "last_activity" in summary
        assert "started_at" in summary

    def test_archive_summary_no_checkpoints(self, tmp_path: Path) -> None:
        """summary.yaml is created correctly even when no checkpoints exist."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            auto_close_stale_runs(ttl_hours=48)

        summary = _reader.read_yaml(run_dir / "meta" / "summary.yaml")
        assert summary["checkpoints_count"] == 0
        assert summary["events_count"] == 0

    def test_archive_summary_counts_survive_torn_events_line(self, tmp_path: Path) -> None:
        """A torn events.jsonl line drops one record, not the whole count.

        events_count / checkpoints_count are advisory archive metadata. Before
        the resilient-reader swap, a strict ``read_jsonl`` raised ``StateError``
        on the first malformed line, which the count helper caught by leaving
        the count at 0 — so one torn concurrent append zeroed the archive tally.
        The resilient reader skips just the torn row, so the intact events are
        still counted (regression guard).
        """
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)

        # Three intact events plus a torn middle append.
        _add_events(run_dir, count=3)
        events_path = run_dir / "meta" / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write('{"ts": "2026-02-11T12:00:00Z", "event": "tor\n')  # truncated mid-object

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 1
        summary = _reader.read_yaml(run_dir / "meta" / "summary.yaml")
        # The torn line is dropped; the three intact events are still counted.
        assert summary["events_count"] == 3


class TestStaleRunNonActiveSkipped:
    """Runs that are not in 'active' status should never be closed."""

    @pytest.mark.parametrize("status", ["completed", "abandoned", "failed"])
    def test_stale_run_non_active_skipped(self, tmp_path: Path, status: str) -> None:
        """A run with non-active status is never touched, even if very old."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(200)
        run_dir = _create_run(runs_root, "test-task", run_id, status=status)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0
        assert result["runs_closed"] == []

        data = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == status
