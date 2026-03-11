"""Tests for maintenance features: auto_close_stale_runs and ceremony integration.

Covers:
- auto_close_stale_runs: stale close, recent skip, non-active skip,
  missing task_root, custom age_days, multiple runs, unparseable timestamp
- TRWConfig: new maintenance fields (defaults and env-var override)
- trw_session_start: auto-close enabled/disabled/fail-open (Step 5)
- trw_deliver: auto-prune enabled/disabled/fail-open (Step 2.5)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import get_tools_sync

import trw_mcp.state.analytics_report as analytics_mod
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.analytics_report import auto_close_stale_runs
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_id(days_ago: int) -> str:
    """Build a run_id whose embedded timestamp is `days_ago` days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ts = dt.strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-abcd1234"


def _create_run(
    task_root: Path,
    task_name: str,
    run_id: str,
    status: str = "active",
) -> Path:
    """Create a run directory with run.yaml at the expected path."""
    run_dir = task_root / task_name / "runs" / run_id / "meta"
    run_dir.mkdir(parents=True)
    _writer.write_yaml(run_dir / "run.yaml", {
        "run_id": run_id,
        "task": task_name,
        "status": status,
        "phase": "implement",
    })
    return run_dir.parent


# ---------------------------------------------------------------------------
# Feature 1: auto_close_stale_runs (analytics_report.py)
# ---------------------------------------------------------------------------


class TestAutoCloseStaleRuns:
    """Unit tests for auto_close_stale_runs()."""

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_stale_active_run_gets_closed(self, tmp_path: Path) -> None:
        """A run older than the threshold with status=active is abandoned."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=10)  # 10 days old, threshold default=7
        run_dir = _create_run(task_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])
        assert result["errors"] == []

        # Verify run.yaml was updated
        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert updated["status"] == "abandoned"
        assert "abandoned_at" in updated
        assert "abandoned_reason" in updated
        assert "Stale timeout" in str(updated["abandoned_reason"])

    def test_recent_active_run_is_not_closed(self, tmp_path: Path) -> None:
        """A run younger than the threshold is skipped even if active."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=0)  # 0 days old (< 48h threshold)
        _create_run(task_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert result["runs_closed"] == []

        # run.yaml must remain unchanged
        data = _reader.read_yaml(tmp_path / "docs" / "my-task" / "runs" / run_id / "meta" / "run.yaml")
        assert data["status"] == "active"

    def test_non_active_run_is_skipped(self, tmp_path: Path) -> None:
        """Completed or abandoned runs are not touched, even when very old."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=30)
        _create_run(task_root, "my-task", run_id, status="complete")

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0

    def test_abandoned_run_is_skipped(self, tmp_path: Path) -> None:
        """Already-abandoned runs are skipped."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=30)
        _create_run(task_root, "my-task", run_id, status="abandoned")

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_missing_task_root_returns_empty_result(self, tmp_path: Path) -> None:
        """If the task_root directory doesn't exist, return empty result."""
        # tmp_path/docs does NOT exist
        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result == {"runs_closed": [], "count": 0, "errors": []}

    def test_custom_age_days_overrides_config(self, tmp_path: Path) -> None:
        """age_days parameter overrides the config default."""
        task_root = tmp_path / "docs"
        # 5 days old — normally below default threshold of 7
        run_id = _make_run_id(days_ago=5)
        _create_run(task_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            # With custom threshold of 3 days, the 5-day-old run should be closed
            result = auto_close_stale_runs(age_days=3)

        assert result["count"] == 1
        assert run_id in cast("list[str]", result["runs_closed"])

    def test_custom_age_days_keeps_run_if_not_old_enough(self, tmp_path: Path) -> None:
        """age_days=30 keeps a 10-day-old run open."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=10)
        _create_run(task_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs(age_days=30)

        assert result["count"] == 0

    def test_multiple_stale_runs_all_closed(self, tmp_path: Path) -> None:
        """All stale active runs across multiple tasks are closed."""
        task_root = tmp_path / "docs"
        run_ids = [
            _make_run_id(days_ago=8),
            _make_run_id(days_ago=15),
            _make_run_id(days_ago=20),
        ]
        # Use distinct task names to avoid run_id collisions between tasks
        for i, rid in enumerate(run_ids):
            _create_run(task_root, f"task-{i}", rid)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 3
        for rid in run_ids:
            assert rid in cast("list[str]", result["runs_closed"])

    def test_unparseable_run_id_timestamp_is_skipped(self, tmp_path: Path) -> None:
        """Run whose run_id has an unparseable timestamp is skipped, not closed."""
        task_root = tmp_path / "docs"
        bad_run_id = "not-a-timestamp-abcd1234"
        run_dir = task_root / "my-task" / "runs" / bad_run_id / "meta"
        run_dir.mkdir(parents=True)
        _writer.write_yaml(run_dir / "run.yaml", {
            "run_id": bad_run_id,
            "task": "my-task",
            "status": "active",
            "phase": "implement",
        })

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert result["errors"] == []

    def test_run_dir_without_run_yaml_is_skipped(self, tmp_path: Path) -> None:
        """Run directory missing run.yaml does not raise and is skipped."""
        task_root = tmp_path / "docs"
        run_dir = task_root / "my-task" / "runs" / "20260101T120000Z-noyaml" / "meta"
        run_dir.mkdir(parents=True)
        # No run.yaml written

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0

    def test_mixed_stale_and_recent_runs_only_stale_closed(self, tmp_path: Path) -> None:
        """Only the stale run is closed; the recent run is left intact."""
        task_root = tmp_path / "docs"
        stale_id = _make_run_id(days_ago=14)
        recent_id = _make_run_id(days_ago=0)  # 0 days old (< 48h threshold)
        _create_run(task_root, "task-stale", stale_id)
        _create_run(task_root, "task-recent", recent_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 1
        closed = cast("list[str]", result["runs_closed"])
        assert stale_id in closed
        assert recent_id not in closed

    def test_abandoned_reason_contains_age_and_threshold(self, tmp_path: Path) -> None:
        """The abandoned_reason field includes the age and threshold."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=10)
        run_dir = _create_run(task_root, "my-task", run_id)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            auto_close_stale_runs()

        updated = _reader.read_yaml(run_dir / "meta" / "run.yaml")
        reason = str(updated["abandoned_reason"])
        assert "threshold" in reason
        assert "48" in reason  # default threshold is 48h

    def test_error_reading_yaml_captured_in_errors(self, tmp_path: Path) -> None:
        """If reading run.yaml raises, the error is captured in the errors list."""
        task_root = tmp_path / "docs"
        run_id = _make_run_id(days_ago=10)
        run_dir = task_root / "my-task" / "runs" / run_id / "meta"
        run_dir.mkdir(parents=True)
        # Write corrupt YAML
        (run_dir / "run.yaml").write_text("{invalid yaml[", encoding="utf-8")

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert len(cast("list[str]", result["errors"])) == 1

    def test_returns_correct_keys(self, tmp_path: Path) -> None:
        """Result always contains runs_closed, count, and errors keys."""
        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert "runs_closed" in result
        assert "count" in result
        assert "errors" in result

    def test_task_dir_without_runs_subdir_is_skipped(self, tmp_path: Path) -> None:
        """Task directories that have no 'runs' subdir are silently skipped."""
        task_root = tmp_path / "docs"
        # Create a task dir that has no runs/ directory
        (task_root / "task-no-runs").mkdir(parents=True)

        with (
            patch.object(analytics_mod, "_config", TRWConfig()),
            patch("trw_mcp.state.analytics_report.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics_report.get_config", return_value=TRWConfig()),
        ):
            result = auto_close_stale_runs()

        assert result["count"] == 0
        assert result["errors"] == []


# ---------------------------------------------------------------------------
# Feature 2: TRWConfig — new maintenance fields
# ---------------------------------------------------------------------------


class TestTRWConfigMaintenanceFields:
    """Validate new config fields introduced for maintenance features."""

    def test_run_auto_close_enabled_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.run_auto_close_enabled is True

    def test_run_auto_close_age_days_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.run_auto_close_age_days == 7

    def test_learning_auto_prune_on_deliver_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_on_deliver is True

    def test_learning_auto_prune_cap_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_cap == 150

    def test_run_auto_close_enabled_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_RUN_AUTO_CLOSE_ENABLED", "false")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.run_auto_close_enabled is False

    def test_run_auto_close_age_days_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_RUN_AUTO_CLOSE_AGE_DAYS", "14")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.run_auto_close_age_days == 14

    def test_learning_auto_prune_on_deliver_env_override(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRW_LEARNING_AUTO_PRUNE_ON_DELIVER", "false")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_on_deliver is False

    def test_learning_auto_prune_cap_env_override(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRW_LEARNING_AUTO_PRUNE_CAP", "200")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_cap == 200


# ---------------------------------------------------------------------------
# Feature 3: ceremony.py integration — session_start auto-close
# ---------------------------------------------------------------------------


class TestSessionStartAutoClose:
    """trw_session_start Step 5: auto_close_stale_runs integration."""

    @staticmethod
    def _get_session_start_fn() -> object:
        """Register ceremony tools on a minimal FastMCP server and return the tool."""
        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        tool = get_tools_sync(server)["trw_session_start"]
        return getattr(tool, "fn", tool)

    def test_session_start_calls_auto_close_when_enabled(
        self, tmp_path: Path,
    ) -> None:
        """When run_auto_close_enabled=True, auto_close_stale_runs is called and
        the result is surfaced in the return value when count > 0."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", True)

        import trw_mcp.state.analytics_report as ar_mod
        import trw_mcp.tools.ceremony as ceremony_mod

        close_result = {"runs_closed": ["run-001"], "count": 1, "errors": []}
        original_fn = ar_mod.auto_close_stale_runs
        mock_close = MagicMock(return_value=close_result)

        fn = self._get_session_start_fn()

        try:
            # Step 5 uses a function-local import: patch at the source module
            ar_mod.auto_close_stale_runs = mock_close  # type: ignore[method-assign]
            with (
                patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
                patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
                patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
                patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
                patch("trw_mcp.tools.ceremony._writer"),
                patch("trw_mcp.tools.ceremony._events"),
            ):
                result = fn()
        finally:
            ar_mod.auto_close_stale_runs = original_fn  # type: ignore[method-assign]

        mock_close.assert_called_once()
        # count > 0 means stale_runs_closed is populated in the result
        assert result.get("stale_runs_closed") == close_result

    def test_session_start_does_not_call_auto_close_when_disabled(
        self, tmp_path: Path,
    ) -> None:
        """When run_auto_close_enabled=False, auto_close_stale_runs is never called."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", False)

        import trw_mcp.tools.ceremony as ceremony_mod

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._writer"),
            patch("trw_mcp.tools.ceremony._events"),
        ):
            mock_close = MagicMock(return_value={"runs_closed": [], "count": 0, "errors": []})
            with patch("trw_mcp.state.analytics_report.auto_close_stale_runs", mock_close):
                fn = self._get_session_start_fn()
                result = fn()

        # auto_close_stale_runs should not have been called
        mock_close.assert_not_called()
        assert "stale_runs_closed" not in result

    def test_session_start_auto_close_exception_is_fail_open(
        self, tmp_path: Path,
    ) -> None:
        """If auto_close_stale_runs raises, session_start still succeeds."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "run_auto_close_enabled", True)

        import trw_mcp.tools.ceremony as ceremony_mod

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch("trw_mcp.tools.ceremony._writer"),
            patch("trw_mcp.tools.ceremony._events"),
        ):
            # Patch the function imported inside Step 5 body
            import trw_mcp.state.analytics_report as ar_mod
            original_fn = ar_mod.auto_close_stale_runs
            try:
                ar_mod.auto_close_stale_runs = MagicMock(  # type: ignore[method-assign]
                    side_effect=RuntimeError("disk full")
                )
                fn = self._get_session_start_fn()
                result = fn()
            finally:
                ar_mod.auto_close_stale_runs = original_fn  # type: ignore[method-assign]

        # session_start must not propagate the exception
        assert result is not None
        # stale_runs_closed key is absent (exception was suppressed)
        assert "stale_runs_closed" not in result


# ---------------------------------------------------------------------------
# Feature 4: ceremony.py integration — trw_deliver auto-prune
# ---------------------------------------------------------------------------


class TestDeliverAutoPrune:
    """trw_deliver Step 2.5: auto_prune_excess_entries integration."""

    def _make_deliver_fn(self) -> object:
        """Register ceremony tools and return the trw_deliver callable."""
        from fastmcp import FastMCP

        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        return get_tools_sync(server)["trw_deliver"].fn

    def _base_patches(self, tmp_path: Path, cfg: TRWConfig) -> dict[str, object]:
        """Return a dict of common patch targets for trw_deliver tests."""
        return {
            "trw_mcp.tools.ceremony.get_config": lambda: cfg,
            "trw_mcp.tools.ceremony.resolve_trw_dir": lambda: tmp_path / ".trw",
            "trw_mcp.tools.ceremony.find_active_run": lambda: None,
            "trw_mcp.tools.ceremony._do_reflect": lambda *a, **kw: {"status": "success", "events_analyzed": 0, "learnings_produced": 0, "success_patterns": 0},
            "trw_mcp.tools.ceremony._do_checkpoint": lambda *a, **kw: None,
            "trw_mcp.tools.ceremony._do_claude_md_sync": lambda *a, **kw: {"status": "success", "learnings_promoted": 0, "total_lines": 0},
            "trw_mcp.tools.ceremony._do_index_sync": lambda: {"status": "success"},
            "trw_mcp.tools.ceremony._do_auto_progress": lambda *a, **kw: {"status": "skipped"},
        }

    def test_deliver_calls_auto_prune_when_enabled(self, tmp_path: Path) -> None:
        """When learning_auto_prune_on_deliver=True, auto_prune_excess_entries is invoked.

        Auto-prune is a deferred step — test it via _run_deferred_steps directly.
        """
        from trw_mcp.tools.ceremony import _run_deferred_steps

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", True)
        object.__setattr__(cfg, "learning_auto_prune_cap", 150)

        import trw_mcp.tools.ceremony as ceremony_mod
        prune_result = {"actions_taken": 5, "status": "pruned"}
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        noop: dict[str, object] = {"status": "skipped"}
        mock_prune = MagicMock(return_value=prune_result)

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony._step_consolidation", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_tier_sweep", return_value=noop),
            patch("trw_mcp.tools.ceremony._do_index_sync", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_auto_progress", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_publish_learnings", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_outcome_correlation", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_recall_outcome", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_telemetry", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_batch_send", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_trust_increment", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_ceremony_feedback", return_value=noop),
        ):
            import trw_mcp.state.analytics as analytics_mod_state
            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = mock_prune  # type: ignore[method-assign]
                _run_deferred_steps(trw_dir, None, {})
            finally:
                analytics_mod_state.auto_prune_excess_entries = original  # type: ignore[method-assign]

        mock_prune.assert_called_once()
        # Check log file for auto_prune result
        import json
        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert "auto_prune" in entry["results"]

    def test_deliver_does_not_call_auto_prune_when_disabled(
        self, tmp_path: Path,
    ) -> None:
        """When learning_auto_prune_on_deliver=False, auto_prune_excess_entries is not called."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", False)

        import trw_mcp.tools.ceremony as ceremony_mod

        fn = self._make_deliver_fn()
        mock_prune = MagicMock(return_value={"actions_taken": 0})

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.tools.ceremony._do_reflect", return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0, "success_patterns": 0}),
            patch("trw_mcp.tools.ceremony._do_claude_md_sync", return_value={"status": "success", "learnings_promoted": 0, "total_lines": 0}),
            patch("trw_mcp.tools.ceremony._do_index_sync", return_value={"status": "success"}),
            patch("trw_mcp.tools.ceremony._do_auto_progress", return_value={"status": "skipped"}),
        ):
            import trw_mcp.state.analytics as analytics_mod_state
            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = mock_prune  # type: ignore[method-assign]
                result = fn(skip_reflect=False, skip_index_sync=False)
            finally:
                analytics_mod_state.auto_prune_excess_entries = original  # type: ignore[method-assign]

        mock_prune.assert_not_called()

    def test_deliver_auto_prune_exception_is_fail_open(
        self, tmp_path: Path,
    ) -> None:
        """If auto_prune_excess_entries raises, deferred steps still continue.

        Auto-prune is a deferred step — test via _run_deferred_steps directly.
        """
        from trw_mcp.tools.ceremony import _run_deferred_steps

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", True)
        object.__setattr__(cfg, "learning_auto_prune_cap", 150)

        import trw_mcp.tools.ceremony as ceremony_mod
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        noop: dict[str, object] = {"status": "skipped"}

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony._step_consolidation", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_tier_sweep", return_value=noop),
            patch("trw_mcp.tools.ceremony._do_index_sync", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_auto_progress", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_publish_learnings", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_outcome_correlation", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_recall_outcome", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_telemetry", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_batch_send", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_trust_increment", return_value=noop),
            patch("trw_mcp.tools.ceremony._step_ceremony_feedback", return_value=noop),
        ):
            import trw_mcp.state.analytics as analytics_mod_state
            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = MagicMock(  # type: ignore[method-assign]
                    side_effect=RuntimeError("storage error")
                )
                _run_deferred_steps(trw_dir, None, {})
            finally:
                analytics_mod_state.auto_prune_excess_entries = original  # type: ignore[method-assign]

        # Check log: auto_prune should show failure, but deferred run completes
        import json
        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["results"]["auto_prune"]["status"] == "failed"
        assert not entry["success"]  # errors list is non-empty

    def test_deliver_auto_prune_cap_passed_correctly(
        self, tmp_path: Path,
    ) -> None:
        """auto_prune_excess_entries is called with max_entries=config.learning_auto_prune_cap."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_auto_prune_on_deliver", True)
        object.__setattr__(cfg, "learning_auto_prune_cap", 200)

        import trw_mcp.tools.ceremony as ceremony_mod

        fn = self._make_deliver_fn()
        mock_prune = MagicMock(return_value={"actions_taken": 0})

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.tools.ceremony._do_reflect", return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0, "success_patterns": 0}),
            patch("trw_mcp.tools.ceremony._do_claude_md_sync", return_value={"status": "success", "learnings_promoted": 0, "total_lines": 0}),
            patch("trw_mcp.tools.ceremony._do_index_sync", return_value={"status": "success"}),
            patch("trw_mcp.tools.ceremony._do_auto_progress", return_value={"status": "skipped"}),
        ):
            import trw_mcp.state.analytics as analytics_mod_state
            original = analytics_mod_state.auto_prune_excess_entries
            try:
                analytics_mod_state.auto_prune_excess_entries = mock_prune  # type: ignore[method-assign]
                fn(skip_reflect=False, skip_index_sync=False)
            finally:
                analytics_mod_state.auto_prune_excess_entries = original  # type: ignore[method-assign]

        # Verify the cap was forwarded correctly
        call_kwargs = mock_prune.call_args
        assert call_kwargs is not None
        # max_entries can be positional or keyword
        args, kwargs = call_kwargs
        passed_cap = kwargs.get("max_entries") or (args[1] if len(args) > 1 else None)
        assert passed_cap == 200
