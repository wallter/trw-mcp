"""Tests for PRD-FIX-028: Stale Run Cleanup & Auto-Archive.

Covers:
- Hour-level TTL-based stale run detection
- Checkpoint-based TTL extension
- Archive summary.yaml creation
- Within-TTL runs are not closed
- Non-active runs are skipped
- Stale count reporting in trw_status
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

import trw_mcp.state.analytics_report as analytics_mod
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics_report import (
    _get_last_activity_timestamp,
    _write_archive_summary,
    auto_close_stale_runs,
    count_stale_runs,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_id_hours_ago(hours_ago: float) -> str:
    """Build a run_id whose embedded timestamp is `hours_ago` hours in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ts = dt.strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-abcd1234"


def _create_run(
    task_root: Path,
    task_name: str,
    run_id: str,
    status: str = "active",
    phase: str = "implement",
) -> Path:
    """Create a run directory with run.yaml at the expected path."""
    run_dir = task_root / task_name / "runs" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    _writer.write_yaml(meta / "run.yaml", {
        "run_id": run_id,
        "task": task_name,
        "status": status,
        "phase": phase,
    })
    # Create empty events.jsonl
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return run_dir


def _add_checkpoint(run_dir: Path, hours_ago: float) -> None:
    """Add a checkpoint entry to a run's checkpoints.jsonl."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    checkpoint = {"ts": ts, "message": "test checkpoint", "state": {}}
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    line = json.dumps(checkpoint) + "\n"
    with checkpoints_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _add_events(run_dir: Path, count: int) -> None:
    """Add event entries to a run's events.jsonl."""
    events_path = run_dir / "meta" / "events.jsonl"
    for i in range(count):
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": f"test_event_{i}",
        }
        line = json.dumps(event) + "\n"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def _patch_config_and_root(tmp_path: Path, ttl_hours: int = 48):
    """Return context managers patching project root and config for tests."""
    cfg = TRWConfig(run_stale_ttl_hours=ttl_hours)
    return (
        patch.object(analytics_mod, "_config", cfg),
        patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics_report.get_config", return_value=cfg),
    )


# ---------------------------------------------------------------------------
# Tests: Hour-Level TTL Detection
# ---------------------------------------------------------------------------


class TestStaleRunHourLevelTTL:
    """auto_close_stale_runs detects runs exceeding hour-level TTL."""

    def test_stale_run_hour_level_ttl(self, tmp_path: Path) -> None:
        """A run older than 48h with no checkpoints is closed."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(72)  # 72h old, threshold 48h
        run_dir = _create_run(task_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])

        # Verify run.yaml was updated with abandon metadata
        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert updated["status"] == "abandoned"
        assert "abandoned_at" in updated
        assert "abandoned_reason" in updated
        assert "threshold: 48h" in str(updated["abandoned_reason"])
        assert updated.get("original_phase") == "implement"

    def test_stale_run_within_ttl(self, tmp_path: Path) -> None:
        """A run within the TTL window is not closed."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(24)  # 24h old, threshold 48h
        _create_run(task_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0
        assert result["runs_closed"] == []


# ---------------------------------------------------------------------------
# Tests: Checkpoint Extends TTL
# ---------------------------------------------------------------------------


class TestStaleRunCheckpointExtendsTTL:
    """A recent checkpoint resets the stale clock."""

    def test_stale_run_checkpoint_extends_ttl(self, tmp_path: Path) -> None:
        """An old run with a recent checkpoint is NOT closed."""
        task_root = tmp_path / "docs"
        # Run created 72h ago (past the 48h threshold)
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(task_root, "test-task", run_id)

        # But has a checkpoint from 12h ago (within threshold)
        _add_checkpoint(run_dir, hours_ago=12)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0
        assert result["runs_closed"] == []

        # Verify run.yaml status still active
        data = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == "active"

    def test_old_checkpoint_does_not_extend_ttl(self, tmp_path: Path) -> None:
        """A run with only old checkpoints is still closed."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(96)
        run_dir = _create_run(task_root, "test-task", run_id)

        # Checkpoint also old (72h ago, still past 48h threshold)
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
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(96)
        run_dir = _create_run(task_root, "test-task", run_id)

        # Old checkpoint (72h ago) and recent checkpoint (6h ago)
        _add_checkpoint(run_dir, hours_ago=72)
        _add_checkpoint(run_dir, hours_ago=6)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        # Most recent checkpoint is 6h ago, within 48h threshold
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# Tests: Archive Summary
# ---------------------------------------------------------------------------


class TestStaleRunArchiveSummary:
    """auto_close_stale_runs writes summary.yaml when closing stale runs."""

    def test_stale_run_archive_summary(self, tmp_path: Path) -> None:
        """summary.yaml is created with correct fields when a run is closed."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(task_root, "test-task", run_id)

        # Add some events and checkpoints
        _add_events(run_dir, count=5)
        _add_checkpoint(run_dir, hours_ago=60)
        _add_checkpoint(run_dir, hours_ago=55)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 1

        # Verify summary.yaml exists and has correct fields
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
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(task_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            auto_close_stale_runs(ttl_hours=48)

        summary = _reader.read_yaml(run_dir / "meta" / "summary.yaml")
        assert summary["checkpoints_count"] == 0
        assert summary["events_count"] == 0


# ---------------------------------------------------------------------------
# Tests: Non-Active Runs Skipped
# ---------------------------------------------------------------------------


class TestStaleRunNonActiveSkipped:
    """Runs that are not in 'active' status should never be closed."""

    @pytest.mark.parametrize("status", ["completed", "abandoned", "failed"])
    def test_stale_run_non_active_skipped(
        self, tmp_path: Path, status: str,
    ) -> None:
        """A run with non-active status is never touched, even if very old."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(200)  # Very old
        run_dir = _create_run(task_root, "test-task", run_id, status=status)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            result = auto_close_stale_runs(ttl_hours=48)

        assert result["count"] == 0
        assert result["runs_closed"] == []

        # Verify status was not changed
        data = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == status


# ---------------------------------------------------------------------------
# Tests: count_stale_runs (read-only)
# ---------------------------------------------------------------------------


class TestCountStaleRuns:
    """count_stale_runs returns stale count without closing anything."""

    def test_count_stale_runs_basic(self, tmp_path: Path) -> None:
        """Counts active runs past the TTL."""
        task_root = tmp_path / "docs"
        # 2 stale runs
        _create_run(task_root, "task-a", _make_run_id_hours_ago(72))
        _create_run(task_root, "task-b", _make_run_id_hours_ago(96))
        # 1 recent run
        _create_run(task_root, "task-c", _make_run_id_hours_ago(12))
        # 1 completed run (old, should be skipped)
        _create_run(
            task_root, "task-d", _make_run_id_hours_ago(200),
            status="completed",
        )

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            count = count_stale_runs(ttl_hours=48)

        assert count == 2

    def test_count_stale_runs_empty(self, tmp_path: Path) -> None:
        """Returns 0 when no task root exists."""
        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            count = count_stale_runs(ttl_hours=48)

        assert count == 0

    def test_count_stale_runs_does_not_modify(self, tmp_path: Path) -> None:
        """count_stale_runs does not modify any run.yaml files."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(task_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            count = count_stale_runs(ttl_hours=48)

        assert count == 1
        # Verify run.yaml was NOT modified
        data = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == "active"
        assert "abandoned_at" not in data

    def test_count_with_checkpoint_extends_ttl(self, tmp_path: Path) -> None:
        """A run with a recent checkpoint is not counted as stale."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(task_root, "test-task", run_id)
        _add_checkpoint(run_dir, hours_ago=6)  # Recent checkpoint

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            count = count_stale_runs(ttl_hours=48)

        assert count == 0


# ---------------------------------------------------------------------------
# Tests: Stale Count in trw_status
# ---------------------------------------------------------------------------


class TestStaleCountInStatus:
    """trw_status includes stale_count in its response."""

    def test_stale_count_in_status(
        self, tmp_path: Path, sample_run_dir: Path,
    ) -> None:
        """trw_status response includes stale_count field."""
        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)

        # Get the trw_status tool
        tools = server._tool_manager._tools
        status_tool = next(
            t for t in tools.values() if t.name == "trw_status"
        )

        # Mock count_stale_runs to return a known value
        with (
            patch(
                "trw_mcp.tools.orchestration.resolve_run_path",
                return_value=sample_run_dir,
            ),
            patch(
                "trw_mcp.tools.orchestration.count_stale_runs",
                return_value=3,
            ) as mock_count,
        ):
            result = status_tool.fn()

        assert "stale_count" in result
        assert result["stale_count"] == 3
        assert "stale_runs_advisory" in result
        assert "3 stale run(s)" in str(result["stale_runs_advisory"])

    def test_stale_count_zero_no_advisory(
        self, tmp_path: Path, sample_run_dir: Path,
    ) -> None:
        """When stale count is 0, no advisory is shown."""
        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)

        tools = server._tool_manager._tools
        status_tool = next(
            t for t in tools.values() if t.name == "trw_status"
        )

        with (
            patch(
                "trw_mcp.tools.orchestration.resolve_run_path",
                return_value=sample_run_dir,
            ),
            patch(
                "trw_mcp.tools.orchestration.count_stale_runs",
                return_value=0,
            ),
        ):
            result = status_tool.fn()

        assert result["stale_count"] == 0
        assert "stale_runs_advisory" not in result

    def test_stale_count_error_failopen(
        self, tmp_path: Path, sample_run_dir: Path,
    ) -> None:
        """When count_stale_runs raises, trw_status still returns normally."""
        from fastmcp import FastMCP

        from trw_mcp.tools.orchestration import register_orchestration_tools

        server = FastMCP("test")
        register_orchestration_tools(server)

        tools = server._tool_manager._tools
        status_tool = next(
            t for t in tools.values() if t.name == "trw_status"
        )

        with (
            patch(
                "trw_mcp.tools.orchestration.resolve_run_path",
                return_value=sample_run_dir,
            ),
            patch(
                "trw_mcp.tools.orchestration.count_stale_runs",
                side_effect=OSError("disk failure"),
            ),
        ):
            result = status_tool.fn()

        # Should still return a valid result without stale_count
        assert "run_id" in result
        assert "stale_count" not in result


# ---------------------------------------------------------------------------
# Tests: Internal helpers
# ---------------------------------------------------------------------------


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
        # Should be approximately 10 hours ago
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


# ---------------------------------------------------------------------------
# Tests: Config field
# ---------------------------------------------------------------------------


class TestConfigRunStaleTTLHours:
    """TRWConfig.run_stale_ttl_hours field exists with correct default."""

    def test_default_value(self) -> None:
        """Default run_stale_ttl_hours is 48."""
        cfg = TRWConfig()
        assert cfg.run_stale_ttl_hours == 48

    def test_custom_value(self) -> None:
        """run_stale_ttl_hours can be set to a custom value."""
        cfg = TRWConfig(run_stale_ttl_hours=24)
        assert cfg.run_stale_ttl_hours == 24
