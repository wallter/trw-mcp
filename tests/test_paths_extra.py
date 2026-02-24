"""Additional path resolution tests — find_active_run and detect_current_phase.

Covers lines 162-187 in state/_paths.py that were at 70%.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._paths import detect_current_phase, find_active_run
from trw_mcp.state.persistence import FileStateWriter


# ---------------------------------------------------------------------------
# Helpers
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
    run_dir = base / task / "runs" / run_id
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
    """Tests for find_active_run() — lexicographic most-recent run."""

    def test_no_task_root_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        task_root = project / "docs"
        run = _make_run(task_root, "task1", "20260219T100000Z-aaa", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run

    def test_returns_lexicographically_latest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns run with lexicographically largest run_id (ISO timestamp)."""
        project = tmp_path / "project"
        task_root = project / "docs"
        _make_run(task_root, "task1", "20260219T100000Z-aaa", writer=writer)
        run2 = _make_run(task_root, "task1", "20260220T100000Z-bbb", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run2

    def test_across_multiple_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns latest run across multiple task directories."""
        project = tmp_path / "project"
        task_root = project / "docs"
        _make_run(task_root, "task-a", "20260219T100000Z-aaa", writer=writer)
        run2 = _make_run(task_root, "task-b", "20260221T120000Z-bbb", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run2

    def test_dirs_without_run_yaml_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Directories without meta/run.yaml are ignored."""
        project = tmp_path / "project"
        task_root = project / "docs"
        # run directory with no run.yaml
        bad_run = task_root / "task1" / "runs" / "20260220T120000Z-bad"
        (bad_run / "meta").mkdir(parents=True)
        # valid run
        good_run = _make_run(task_root, "task1", "20260219T100000Z-good", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == good_run

    def test_task_dir_without_runs_subdir_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Task directories without runs/ subdirectory are skipped."""
        project = tmp_path / "project"
        task_root = project / "docs"
        # task without runs/ dir
        (task_root / "empty-task").mkdir(parents=True)
        # valid task
        valid_run = _make_run(task_root, "valid-task", "20260219T100000Z-valid", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == valid_run

    def test_empty_task_root_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty task_root with no subdirs returns None."""
        project = tmp_path / "project"
        (project / "docs").mkdir(parents=True)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result is None

    def test_oserror_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
    """Tests for detect_current_phase() — reads phase from latest run.yaml."""

    def test_no_task_root_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        task_root = project / "docs"
        _make_run(task_root, "task1", "20260219T100000Z-aaa", phase="implement", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result == "implement"

    def test_inactive_run_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns None when run status is not 'active'."""
        project = tmp_path / "project"
        task_root = project / "docs"
        _make_run(task_root, "task1", "20260219T100000Z-done", status="complete", phase="deliver", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_returns_latest_run_phase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Returns phase from lexicographically latest run."""
        project = tmp_path / "project"
        task_root = project / "docs"
        _make_run(task_root, "task1", "20260219T100000Z-old", phase="research", writer=writer)
        _make_run(task_root, "task1", "20260220T100000Z-new", phase="validate", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result == "validate"

    def test_no_runs_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when no run directories exist."""
        project = tmp_path / "project"
        (project / "docs" / "task1" / "runs").mkdir(parents=True)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_missing_phase_field_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when phase field is missing from run.yaml."""
        project = tmp_path / "project"
        task_root = project / "docs"
        run_dir = task_root / "task1" / "runs" / "20260219T100000Z-aaa"
        (run_dir / "meta").mkdir(parents=True)
        # Write YAML with status=active but no phase key
        (run_dir / "meta" / "run.yaml").write_text("status: active\n")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = detect_current_phase()
        assert result is None

    def test_oserror_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError during scan returns None gracefully."""
        def raise_oserror() -> Path:
            raise OSError("permission denied")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", raise_oserror)
        result = detect_current_phase()
        assert result is None
