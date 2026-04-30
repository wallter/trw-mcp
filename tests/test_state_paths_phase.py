"""Tests for phase detection in trw_mcp.state._paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._paths import detect_current_phase, pin_active_run, unpin_active_run
from trw_mcp.state.persistence import FileStateWriter

from ._state_paths_support import _make_run


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
