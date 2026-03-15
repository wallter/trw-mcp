"""Tests for shared path resolution in trw_mcp.state._paths.

Includes additional tests for find_active_run, detect_current_phase,
and process-local run pinning.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._paths import (
    detect_current_phase,
    find_active_run,
    get_pinned_run,
    pin_active_run,
    resolve_run_path,
    unpin_active_run,
)
from trw_mcp.state.persistence import FileStateWriter


class TestResolveProjectRoot:
    """Tests for resolve_project_root()."""

    def test_uses_env_var(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns TRW_PROJECT_ROOT env var when set."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result == tmp_path.resolve()

    def test_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns CWD when env var is not set."""
        import os as _os

        monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)

        # Bypass the autouse _isolate_trw_dir fixture patch by calling the
        # real logic directly (the fixture patches the module-level name).
        def _real_resolve_project_root() -> Path:
            env_root = _os.environ.get("TRW_PROJECT_ROOT")
            if env_root:
                return Path(env_root).resolve()
            return Path.cwd().resolve()

        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            _real_resolve_project_root,
        )

        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result == Path.cwd().resolve()

    def test_resolves_to_absolute(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is always an absolute path."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_project_root

        result = resolve_project_root()
        assert result.is_absolute()


class TestResolveTrwDir:
    """Tests for resolve_trw_dir()."""

    def test_returns_trw_subdir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns project_root / .trw."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        assert result == tmp_path.resolve() / ".trw"

    def test_is_absolute(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Result is always an absolute path."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        from trw_mcp.state._paths import resolve_trw_dir

        result = resolve_trw_dir()
        assert result.is_absolute()


class TestResolveRunPath:
    """Tests for resolve_run_path() — PRD-FIX-007."""

    def test_explicit_path_returns_given(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02a: Explicit run_path resolves when it exists."""
        run = tmp_path / "myrun"
        run.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: tmp_path,
        )
        assert resolve_run_path(str(run)) == run.resolve()

    def test_explicit_nonexistent_raises(self, tmp_path: Path) -> None:
        """FR02a: Non-existent explicit path raises StateError."""
        with pytest.raises(StateError, match="does not exist"):
            resolve_run_path(str(tmp_path / "nonexistent"))

    def test_auto_detect_single_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02b: Auto-detection with single run directory."""
        project = tmp_path / "project"
        run1 = project / ".trw" / "runs" / "task1" / "run-001"
        (run1 / "meta").mkdir(parents=True)
        (run1 / "meta" / "run.yaml").write_text("run_id: run-001\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        assert resolve_run_path() == run1

    def test_auto_detect_most_recent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02b: Auto-detection selects most recently modified run.yaml."""
        project = tmp_path / "project"
        run1 = project / ".trw" / "runs" / "task1" / "run-001"
        (run1 / "meta").mkdir(parents=True)
        (run1 / "meta" / "run.yaml").write_text("run_id: run-001\n")
        os.utime(run1 / "meta" / "run.yaml", (1000, 1000))
        run2 = project / ".trw" / "runs" / "task1" / "run-002"
        (run2 / "meta").mkdir(parents=True)
        (run2 / "meta" / "run.yaml").write_text("run_id: run-002\n")
        os.utime(run2 / "meta" / "run.yaml", (2000, 2000))
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        assert resolve_run_path() == run2

    def test_no_runs_root_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02c: StateError when .trw/runs/ directory not found."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        with pytest.raises(StateError, match=r"\.trw/runs/ directory not found"):
            resolve_run_path()

    def test_empty_runs_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02c: StateError when runs_root exists but contains no run dirs."""
        project = tmp_path / "project"
        (project / ".trw" / "runs" / "task1").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        with pytest.raises(StateError, match="No active runs"):
            resolve_run_path()

    def test_ignores_dirs_without_run_yaml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR02b: Directories without meta/run.yaml are skipped."""
        project = tmp_path / "project"
        # run with meta/ but no run.yaml — should be skipped
        no_yaml = project / ".trw" / "runs" / "task1" / "run-bad"
        (no_yaml / "meta").mkdir(parents=True)
        # run with run.yaml — should be found
        good = project / ".trw" / "runs" / "task1" / "run-good"
        (good / "meta").mkdir(parents=True)
        (good / "meta" / "run.yaml").write_text("run_id: run-good\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        assert resolve_run_path() == good

    def test_error_includes_project_root(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR06: Error context includes project_root for debugging."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        with pytest.raises(StateError) as exc_info:
            resolve_run_path()
        assert "project_root" in exc_info.value.context
        assert str(project) in str(exc_info.value.context["project_root"])

    def test_auto_detect_across_task_dirs(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-detection works across multiple task directories."""
        project = tmp_path / "project"
        run1 = project / ".trw" / "runs" / "task-a" / "run-001"
        (run1 / "meta").mkdir(parents=True)
        (run1 / "meta" / "run.yaml").write_text("run_id: run-001\n")
        os.utime(run1 / "meta" / "run.yaml", (1000, 1000))
        run2 = project / ".trw" / "runs" / "task-b" / "run-002"
        (run2 / "meta").mkdir(parents=True)
        (run2 / "meta" / "run.yaml").write_text("run_id: run-002\n")
        os.utime(run2 / "meta" / "run.yaml", (2000, 2000))
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        assert resolve_run_path() == run2

    def test_explicit_path_returns_absolute(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit path is resolved to absolute."""
        run = tmp_path / "myrun"
        run.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: tmp_path,
        )
        result = resolve_run_path(str(run))
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# Config-driven runs_root wiring
# ---------------------------------------------------------------------------


class TestResolveRunPathConfigWiring:
    """Tests for config-driven runs_root in path resolution."""

    def test_custom_runs_root_auto_detect(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom runs_root='work/runs' finds runs under work/runs/."""
        config = TRWConfig(runs_root="work/runs")
        monkeypatch.setattr("trw_mcp.state._paths.get_config", lambda: config)

        project = tmp_path / "project"
        run = project / "work" / "runs" / "task1" / "run-001"
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text("run_id: run-001\n")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        assert resolve_run_path() == run

    def test_custom_runs_root_error_no_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom runs_root='work/runs' error references 'work/runs/'."""
        config = TRWConfig(runs_root="work/runs")
        monkeypatch.setattr("trw_mcp.state._paths.get_config", lambda: config)

        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        with pytest.raises(StateError, match="work/runs/ directory not found"):
            resolve_run_path()

    def test_custom_runs_root_no_runs_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom runs_root='work/runs' error when no runs found."""
        config = TRWConfig(runs_root="work/runs")
        monkeypatch.setattr("trw_mcp.state._paths.get_config", lambda: config)

        project = tmp_path / "project"
        (project / "work" / "runs" / "task1").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: project,
        )
        with pytest.raises(StateError, match=r"No active runs found in work/runs/"):
            resolve_run_path()


# ---------------------------------------------------------------------------
# Helpers (from test_paths_extra)
# ---------------------------------------------------------------------------


def _make_run(
    base: Path,
    task: str,
    run_id: str,
    status: str = "active",
    phase: str = "implement",
    writer: FileStateWriter | None = None,
) -> Path:
    """Create a minimal run directory with run.yaml."""
    run_dir = base / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    data = {
        "run_id": run_id,
        "task": task,
        "status": status,
        "phase": phase,
    }
    if writer:
        writer.write_yaml(meta / "run.yaml", data)
    else:
        import yaml

        (meta / "run.yaml").write_text(yaml.dump(data))
    return run_dir


# ---------------------------------------------------------------------------
# TestFindActiveRun
# ---------------------------------------------------------------------------


class TestFindActiveRun:
    """Tests for find_active_run() -- lexicographic most-recent run."""

    def test_no_task_root_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when task_root directory does not exist."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result is None

    def test_single_run_returns_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Single run directory is returned."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run = _make_run(runs_root, "task1", "20260219T100000Z-aaa", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run

    def test_returns_lexicographically_latest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns run with lexicographically largest run_id (ISO timestamp)."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(runs_root, "task1", "20260219T100000Z-aaa", writer=writer)
        run2 = _make_run(runs_root, "task1", "20260220T100000Z-bbb", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run2

    def test_across_multiple_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns latest run across multiple task directories."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(runs_root, "task-a", "20260219T100000Z-aaa", writer=writer)
        run2 = _make_run(runs_root, "task-b", "20260221T120000Z-bbb", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run2

    def test_dirs_without_run_yaml_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Directories without meta/run.yaml are ignored."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        bad_run = runs_root / "task1" / "20260220T120000Z-bad"
        (bad_run / "meta").mkdir(parents=True)
        good_run = _make_run(runs_root, "task1", "20260219T100000Z-good", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == good_run

    def test_task_dir_without_run_subdirs_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Empty task directories without run subdirs are skipped."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        (runs_root / "empty-task").mkdir(parents=True)
        valid_run = _make_run(runs_root, "valid-task", "20260219T100000Z-valid", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == valid_run

    def test_empty_runs_root_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty runs_root with no subdirs returns None."""
        project = tmp_path / "project"
        (project / ".trw" / "runs").mkdir(parents=True)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result is None

    def test_oserror_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during scan returns None (graceful degradation)."""

        def raise_oserror() -> Path:
            raise OSError("permission denied")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", raise_oserror)
        result = find_active_run()
        assert result is None


# ---------------------------------------------------------------------------
# TestDetectCurrentPhase
# ---------------------------------------------------------------------------


class TestDetectCurrentPhase:
    """Tests for detect_current_phase() -- reads phase from latest run.yaml."""

    def test_no_task_root_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when task_root directory does not exist."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_active_run_returns_phase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns phase when active run exists."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(runs_root, "task1", "20260219T100000Z-aaa", phase="implement", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result == "implement"

    def test_inactive_run_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns None when run status is not 'active'."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(runs_root, "task1", "20260219T100000Z-done", status="complete", phase="deliver", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_returns_latest_run_phase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns phase from lexicographically latest run."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(runs_root, "task1", "20260219T100000Z-old", phase="research", writer=writer)
        _make_run(runs_root, "task1", "20260220T100000Z-new", phase="validate", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result == "validate"

    def test_skips_completed_runs_returns_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Skips completed/failed runs and returns phase from latest active run."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(runs_root, "task1", "20260219T100000Z-old", phase="implement", writer=writer)
        _make_run(runs_root, "task1", "20260220T100000Z-done", status="complete", phase="deliver", writer=writer)
        _make_run(runs_root, "task1", "20260221T100000Z-fail", status="failed", phase="validate", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        # Should skip the completed and failed runs, return the older active run's phase
        assert result == "implement"

    def test_no_runs_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when no run directories exist."""
        project = tmp_path / "project"
        (project / ".trw" / "runs" / "task1").mkdir(parents=True)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_missing_phase_field_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when phase field is missing from run.yaml."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run_dir = runs_root / "task1" / "20260219T100000Z-aaa"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text("status: active\n")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_oserror_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during scan returns None gracefully."""

        def raise_oserror() -> Path:
            raise OSError("permission denied")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", raise_oserror)
        result = detect_current_phase()
        assert result is None

    def test_pinned_run_returns_phase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """When a run is pinned, detect_current_phase uses pinned run."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        old_run = _make_run(runs_root, "task1", "20260219T100000Z-old", phase="research", writer=writer)
        _make_run(runs_root, "task1", "20260220T100000Z-new", phase="validate", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        pin_active_run(old_run)
        try:
            result = detect_current_phase()
            assert result == "research"
        finally:
            unpin_active_run()

    def test_pinned_run_inactive_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Pinned run with non-active status returns None."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        done_run = _make_run(
            runs_root, "task1", "20260219T100000Z-done", status="complete", phase="deliver", writer=writer
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        pin_active_run(done_run)
        try:
            result = detect_current_phase()
            assert result is None
        finally:
            unpin_active_run()


# ---------------------------------------------------------------------------
# TestPinActiveRun
# ---------------------------------------------------------------------------


class TestPinActiveRun:
    """Tests for process-local run pinning (RC-001 fix)."""

    def setup_method(self) -> None:
        """Ensure clean state before each test."""
        unpin_active_run()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        unpin_active_run()

    def test_pin_overrides_filesystem_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Pinned run is returned instead of filesystem scan result."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        old_run = _make_run(runs_root, "task1", "20260219T100000Z-old", writer=writer)
        _make_run(runs_root, "task1", "20260220T100000Z-new", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        assert find_active_run() != old_run

        pin_active_run(old_run)
        assert find_active_run() == old_run.resolve()

    def test_unpin_reverts_to_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """After unpin, find_active_run reverts to filesystem scan."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        old_run = _make_run(runs_root, "task1", "20260219T100000Z-old", writer=writer)
        new_run = _make_run(runs_root, "task1", "20260220T100000Z-new", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        pin_active_run(old_run)
        assert find_active_run() == old_run.resolve()

        unpin_active_run()
        assert find_active_run() == new_run

    def test_get_pinned_run_reflects_state(self, tmp_path: Path) -> None:
        """get_pinned_run returns current pin state."""
        assert get_pinned_run() is None

        run_dir = tmp_path / "my-run"
        run_dir.mkdir()
        pin_active_run(run_dir)
        assert get_pinned_run() == run_dir.resolve()

        unpin_active_run()
        assert get_pinned_run() is None

    def test_pin_resolves_path(self, tmp_path: Path) -> None:
        """Pin resolves relative-like paths to absolute."""
        run_dir = tmp_path / "a" / ".." / "a" / "run"
        run_dir.mkdir(parents=True)
        pin_active_run(run_dir)
        pinned = get_pinned_run()
        assert pinned is not None
        assert ".." not in str(pinned)

    def test_pin_prevents_cross_instance_hijack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Simulates the parallel-instance hijack scenario from Sprint 28/29."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        run_a = _make_run(runs_root, "sprint-28-track-a", "20260219T100000Z-aaaa", writer=writer)
        pin_active_run(run_a)

        _make_run(runs_root, "sprint-28-track-b", "20260220T120000Z-bbbb", writer=writer)

        assert find_active_run() == run_a.resolve()
