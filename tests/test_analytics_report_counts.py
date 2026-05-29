"""Tests for stale-run counting analytics report behavior."""

from __future__ import annotations

from pathlib import Path

from tests._test_analytics_report_support import (
    _add_checkpoint,
    _create_run,
    _make_run_id_hours_ago,
    _patch_config_and_root,
    _reader,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics.report import count_stale_runs


class TestCountStaleRuns:
    """count_stale_runs returns stale count without closing anything."""

    def test_count_stale_runs_basic(self, tmp_path: Path) -> None:
        """Counts active runs past the TTL."""
        runs_root = tmp_path / ".trw" / "runs"
        _create_run(runs_root, "task-a", _make_run_id_hours_ago(72))
        _create_run(runs_root, "task-b", _make_run_id_hours_ago(96))
        _create_run(runs_root, "task-c", _make_run_id_hours_ago(12))
        _create_run(runs_root, "task-d", _make_run_id_hours_ago(200), status="completed")

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
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            count = count_stale_runs(ttl_hours=48)

        assert count == 1
        data = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == "active"
        assert "abandoned_at" not in data

    def test_count_with_checkpoint_extends_ttl(self, tmp_path: Path) -> None:
        """A run with a recent checkpoint is not counted as stale."""
        runs_root = tmp_path / ".trw" / "runs"
        run_id = _make_run_id_hours_ago(72)
        run_dir = _create_run(runs_root, "test-task", run_id)
        _add_checkpoint(run_dir, hours_ago=6)

        p1, p2, p3 = _patch_config_and_root(tmp_path)
        with p1, p2, p3:
            count = count_stale_runs(ttl_hours=48)

        assert count == 0


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
