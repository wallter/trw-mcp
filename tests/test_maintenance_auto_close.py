"""Tests for maintenance auto-close behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from unittest.mock import patch

import trw_mcp.state.analytics.report as analytics_mod
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics.report import auto_close_stale_runs
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


def _make_run_id(days_ago: int) -> str:
    """Build a run_id whose embedded timestamp is `days_ago` days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ts = dt.strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-abcd1234"


def _create_run(
    runs_root: Path,
    task_name: str,
    run_id: str,
    status: str = "active",
) -> Path:
    """Create a run directory with run.yaml at the expected path."""
    run_dir = runs_root / task_name / run_id / "meta"
    run_dir.mkdir(parents=True)
    _writer.write_yaml(
        run_dir / "run.yaml",
        {
            "run_id": run_id,
            "task": task_name,
            "status": status,
            "phase": "implement",
        },
    )
    return run_dir.parent


class TestAutoCloseStaleRuns:
    """Unit tests for auto_close_stale_runs()."""

    def test_stale_active_run_gets_closed(self, tmp_path: Path) -> None:
        """A run older than the threshold with status=active is abandoned."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=10)
        run_dir = _create_run(runs_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])
        assert result["errors"] == []

        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert updated["status"] == "abandoned"
        assert "abandoned_at" in updated
        assert "abandoned_reason" in updated
        assert "Stale timeout" in str(updated["abandoned_reason"])

    def test_recent_active_run_is_not_closed(self, tmp_path: Path) -> None:
        """A run younger than the threshold is skipped even if active."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=0)
        _create_run(runs_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert result["runs_closed"] == []

        data = _reader.read_yaml(tmp_path / ".trw" / "runs" / "my-task" / run_id / "meta" / "run.yaml")
        assert data["status"] == "active"

    def test_non_active_run_is_skipped(self, tmp_path: Path) -> None:
        """Completed or abandoned runs are not touched, even when very old."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=30)
        _create_run(runs_root, "my-task", run_id, status="complete")

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0

    def test_abandoned_run_is_skipped(self, tmp_path: Path) -> None:
        """Already-abandoned runs are skipped."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=30)
        _create_run(runs_root, "my-task", run_id, status="abandoned")

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0

    def test_missing_task_root_returns_empty_result(self, tmp_path: Path) -> None:
        """If the task_root directory doesn't exist, return empty result."""
        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result == {"runs_closed": [], "count": 0, "errors": []}

    def test_custom_age_days_overrides_config(self, tmp_path: Path) -> None:
        """age_days parameter overrides the config default."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=5)
        _create_run(runs_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs(age_days=3)

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])

    def test_custom_age_days_keeps_run_if_not_old_enough(self, tmp_path: Path) -> None:
        """age_days=30 keeps a 10-day-old run open."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=10)
        _create_run(runs_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs(age_days=30)

        assert result["count"] == 0

    def test_multiple_stale_runs_all_closed(self, tmp_path: Path) -> None:
        """All stale active runs across multiple tasks are closed."""
        runs_root = tmp_path / ".trw" / "runs"
        run_ids = [_make_run_id(days_ago=8), _make_run_id(days_ago=15), _make_run_id(days_ago=20)]
        for i, rid in enumerate(run_ids):
            _create_run(runs_root, f"task-{i}", rid)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 3
        for rid in run_ids:
            assert rid in cast("list[str]", result["runs_closed"])

    def test_unparseable_run_id_timestamp_is_skipped(self, tmp_path: Path) -> None:
        """Run whose run_id has an unparseable timestamp is skipped, not closed."""
        runs_root = tmp_path / ".trw" / "runs"
        bad_run_id = "not-a-timestamp-abcd1234"
        run_dir = runs_root / "my-task" / bad_run_id / "meta"
        run_dir.mkdir(parents=True)
        _writer.write_yaml(
            run_dir / "run.yaml",
            {
                "run_id": bad_run_id,
                "task": "my-task",
                "status": "active",
                "phase": "implement",
            },
        )

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert result["errors"] == []

    def test_run_dir_without_run_yaml_is_skipped(self, tmp_path: Path) -> None:
        """Run directory missing run.yaml does not raise and is skipped."""
        run_dir = tmp_path / ".trw" / "runs" / "my-task" / "20260101T120000Z-noyaml" / "meta"
        run_dir.mkdir(parents=True)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0

    def test_mixed_stale_and_recent_runs_only_stale_closed(self, tmp_path: Path) -> None:
        """Only the stale run is closed; the recent run is left intact."""
        runs_root = tmp_path / ".trw" / "runs"
        stale_id = _make_run_id(days_ago=14)
        recent_id = _make_run_id(days_ago=0)
        _create_run(runs_root, "task-stale", stale_id)
        _create_run(runs_root, "task-recent", recent_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 1
        closed = cast("list[str]", result["runs_closed"])
        assert stale_id in closed
        assert recent_id not in closed

    def test_abandoned_reason_contains_age_and_threshold(self, tmp_path: Path) -> None:
        """The abandoned_reason field includes the age and threshold."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id(days_ago=10)
        run_dir = _create_run(runs_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            auto_close_stale_runs()

        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        reason = str(updated["abandoned_reason"])
        assert "threshold" in reason
        assert "48" in reason

    def test_error_reading_yaml_captured_in_errors(self, tmp_path: Path) -> None:
        """If reading run.yaml raises, the error is captured in the errors list."""
        run_id = _make_run_id(days_ago=10)
        run_dir = tmp_path / ".trw" / "runs" / "my-task" / run_id / "meta"
        run_dir.mkdir(parents=True)
        (run_dir / "run.yaml").write_text("{invalid yaml[", encoding="utf-8")

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert len(cast("list[str]", result["errors"])) == 1

    def test_returns_correct_keys(self, tmp_path: Path) -> None:
        """Result always contains runs_closed, count, and errors keys."""
        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert "runs_closed" in result
        assert "count" in result
        assert "errors" in result

    def test_task_dir_without_runs_subdir_is_skipped(self, tmp_path: Path) -> None:
        """Task directories that have no 'runs' subdir are silently skipped."""
        (tmp_path / ".trw" / "runs" / "task-no-runs").mkdir(parents=True)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert result["errors"] == []
