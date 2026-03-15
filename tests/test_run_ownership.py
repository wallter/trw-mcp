"""Tests for Run Ownership & Session Isolation (PRD-FIX-042)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._paths import (
    _pinned_runs,
    _reset_session_id,
    find_active_run,
    get_pinned_run,
    get_session_id,
    pin_active_run,
    unpin_active_run,
)
from trw_mcp.state.persistence import FileStateWriter


def _make_run(
    base: Path,
    task: str,
    run_id: str,
    status: str = "active",
    phase: str = "implement",
    owner_session_id: str | None = None,
    writer: FileStateWriter | None = None,
) -> Path:
    """Create a minimal run directory with run.yaml."""
    import yaml

    run_dir = base / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    data: dict[str, object] = {
        "run_id": run_id,
        "task": task,
        "status": status,
        "phase": phase,
    }
    if owner_session_id is not None:
        data["owner_session_id"] = owner_session_id
    if writer:
        writer.write_yaml(meta / "run.yaml", data)
    else:
        (meta / "run.yaml").write_text(yaml.dump(data))
    return run_dir


class TestSessionIdentity:
    """FR03: Session identity — unique per process."""

    def test_session_id_is_hex(self) -> None:
        sid = get_session_id()
        assert len(sid) == 32
        int(sid, 16)  # validates hex

    def test_session_id_stable(self) -> None:
        assert get_session_id() == get_session_id()

    def test_reset_session_id(self) -> None:
        original = get_session_id()
        _reset_session_id("test-session-123")
        assert get_session_id() == "test-session-123"
        _reset_session_id(original)
        assert get_session_id() == original

    def test_reset_session_id_none_generates_new(self) -> None:
        original = get_session_id()
        _reset_session_id(None)
        new_id = get_session_id()
        assert new_id != original
        assert len(new_id) == 32
        _reset_session_id(original)


class TestPerSessionPinning:
    """FR06: Per-session run pinning."""

    def setup_method(self) -> None:
        _pinned_runs.clear()

    def teardown_method(self) -> None:
        _pinned_runs.clear()

    def test_pin_uses_default_session(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run1"
        run_dir.mkdir()
        pin_active_run(run_dir)
        assert get_pinned_run() == run_dir.resolve()

    def test_pin_explicit_session(self, tmp_path: Path) -> None:
        run_a = tmp_path / "run-a"
        run_b = tmp_path / "run-b"
        run_a.mkdir()
        run_b.mkdir()

        pin_active_run(run_a, session_id="session-1")
        pin_active_run(run_b, session_id="session-2")

        assert get_pinned_run(session_id="session-1") == run_a.resolve()
        assert get_pinned_run(session_id="session-2") == run_b.resolve()

    def test_unpin_only_affects_session(self, tmp_path: Path) -> None:
        run_a = tmp_path / "run-a"
        run_b = tmp_path / "run-b"
        run_a.mkdir()
        run_b.mkdir()

        pin_active_run(run_a, session_id="session-1")
        pin_active_run(run_b, session_id="session-2")

        unpin_active_run(session_id="session-1")
        assert get_pinned_run(session_id="session-1") is None
        assert get_pinned_run(session_id="session-2") == run_b.resolve()

    def test_find_active_run_uses_session_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        filesystem_run = _make_run(runs_root, "task1", "20260220T100000Z-fs", writer=writer)
        pinned_run = _make_run(runs_root, "task1", "20260219T100000Z-old", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        # Without pin, filesystem returns newer run
        assert find_active_run() == filesystem_run

        # Pin older run for a specific session
        pin_active_run(pinned_run, session_id="my-session")
        # Default session still gets filesystem result
        assert find_active_run() == filesystem_run
        # Specific session gets pinned result
        assert find_active_run(session_id="my-session") == pinned_run.resolve()


class TestStatusAwareDiscovery:
    """FR02: find_active_run only returns active runs."""

    def test_completed_run_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        active_run = _make_run(
            runs_root, "task1", "20260219T100000Z-active",
            status="active", writer=writer,
        )
        _make_run(
            runs_root, "task1", "20260220T100000Z-done",
            status="complete", writer=writer,
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == active_run

    def test_failed_run_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        active_run = _make_run(
            runs_root, "task1", "20260219T100000Z-active",
            status="active", writer=writer,
        )
        _make_run(
            runs_root, "task1", "20260220T100000Z-fail",
            status="failed", writer=writer,
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == active_run

    def test_all_completed_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(
            runs_root, "task1", "20260219T100000Z-done1",
            status="complete", writer=writer,
        )
        _make_run(
            runs_root, "task1", "20260220T100000Z-done2",
            status="complete", writer=writer,
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result is None

    def test_legacy_run_without_status_treated_as_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backward compat: runs without status field are active."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run_dir = runs_root / "task1" / "20260219T100000Z-legacy"
        (run_dir / "meta").mkdir(parents=True)
        # No status field at all
        (run_dir / "meta" / "run.yaml").write_text("run_id: legacy\n")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_active_run()
        assert result == run_dir


class TestRunOwnership:
    """FR04: Run ownership tracking — owner_session_id in RunState."""

    def test_run_state_has_owner_field(self) -> None:
        from trw_mcp.models.run import RunState
        state = RunState(run_id="test", task="test")
        assert state.owner_session_id is None

    def test_run_state_with_owner(self) -> None:
        from trw_mcp.models.run import RunState
        state = RunState(run_id="test", task="test", owner_session_id="abc123")
        assert state.owner_session_id == "abc123"

    def test_owner_roundtrips_through_yaml(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.persistence import FileStateReader, model_to_dict
        from trw_mcp.models.run import RunState

        state = RunState(run_id="test", task="test", owner_session_id="abc123")
        yaml_path = tmp_path / "run.yaml"
        writer.write_yaml(yaml_path, model_to_dict(state))

        reader = FileStateReader()
        data = reader.read_yaml(yaml_path)
        assert data["owner_session_id"] == "abc123"


class TestMarkRunComplete:
    """FR01: trw_deliver marks run as complete."""

    def test_mark_run_complete(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.tools.ceremony import _mark_run_complete
        from trw_mcp.state.persistence import FileStateReader

        run_dir = tmp_path / "run1"
        (run_dir / "meta").mkdir(parents=True)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {"run_id": "test", "status": "active", "phase": "deliver"},
        )

        _mark_run_complete(run_dir)

        reader = FileStateReader()
        data = reader.read_yaml(run_dir / "meta" / "run.yaml")
        assert data["status"] == "complete"

    def test_mark_run_complete_no_yaml_noop(self, tmp_path: Path) -> None:
        from trw_mcp.tools.ceremony import _mark_run_complete

        run_dir = tmp_path / "run-missing"
        (run_dir / "meta").mkdir(parents=True)
        # No run.yaml — should not raise
        _mark_run_complete(run_dir)

    def test_delivered_run_not_recovered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter,
    ) -> None:
        """After marking complete, find_active_run skips the run."""
        from trw_mcp.tools.ceremony import _mark_run_complete

        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run = _make_run(runs_root, "task1", "20260220T100000Z-test", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        assert find_active_run() == run

        _mark_run_complete(run)
        assert find_active_run() is None


class TestOwnershipWarning:
    """FR05: Ownership-aware recovery warnings."""

    def test_get_run_status_includes_owner(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.tools.ceremony import _get_run_status

        run_dir = tmp_path / "run1"
        (run_dir / "meta").mkdir(parents=True)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {"run_id": "test", "status": "active", "owner_session_id": "other-session"},
        )

        status = _get_run_status(run_dir)
        assert status["owner_session_id"] == "other-session"

    def test_get_run_status_no_owner(
        self, tmp_path: Path, writer: FileStateWriter,
    ) -> None:
        from trw_mcp.tools.ceremony import _get_run_status

        run_dir = tmp_path / "run1"
        (run_dir / "meta").mkdir(parents=True)
        writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {"run_id": "test", "status": "active"},
        )

        status = _get_run_status(run_dir)
        assert "owner_session_id" not in status
