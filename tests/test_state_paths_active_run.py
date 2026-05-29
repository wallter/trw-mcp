"""Tests for active run selection in trw_mcp.state._paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._paths import find_run_via_mtime_scan
from trw_mcp.state.persistence import FileStateWriter

from ._state_paths_support import _make_run


class TestFindActiveRun:
    """Tests for find_run_via_mtime_scan() -- lexicographic most-recent run."""

    def test_no_task_root_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when task_root directory does not exist."""
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
        assert result is None

    def test_single_run_returns_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Single run directory is returned."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run = _make_run(runs_root, "task1", "20260219T100000Z-aaa", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
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
        result = find_run_via_mtime_scan()
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
        result = find_run_via_mtime_scan()
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
        result = find_run_via_mtime_scan()
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
        result = find_run_via_mtime_scan()
        assert result == valid_run

    def test_empty_runs_root_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty runs_root with no subdirs returns None."""
        project = tmp_path / "project"
        (project / ".trw" / "runs").mkdir(parents=True)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
        assert result is None

    def test_oserror_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during scan returns None (graceful degradation)."""

        def raise_oserror() -> Path:
            raise OSError("permission denied")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", raise_oserror)
        result = find_run_via_mtime_scan()
        assert result is None
