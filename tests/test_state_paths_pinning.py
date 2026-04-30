"""Tests for process-local run pinning in trw_mcp.state._paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._paths import find_active_run, get_pinned_run, pin_active_run, unpin_active_run
from trw_mcp.state.persistence import FileStateWriter

from ._state_paths_support import _make_run


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
