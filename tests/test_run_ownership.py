"""Tests for Run Ownership & Session Isolation (PRD-FIX-042)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._paths import (
    _pinned_runs,
    _reset_session_id,
    find_active_run,
    find_run_via_mtime_scan,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        filesystem_run = _make_run(runs_root, "task1", "20260220T100000Z-fs", writer=writer)
        pinned_run = _make_run(runs_root, "task1", "20260219T100000Z-old", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        # Without pin, filesystem returns newer run
        assert find_run_via_mtime_scan() == filesystem_run

        # Pin older run for a specific session
        pin_active_run(pinned_run, session_id="my-session")
        # Default session still gets filesystem result
        assert find_run_via_mtime_scan() == filesystem_run
        # Specific session gets pinned result
        assert find_active_run(session_id="my-session") == pinned_run.resolve()


class TestStatusAwareDiscovery:
    """FR02: find_active_run only returns active runs."""

    def test_completed_run_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        active_run = _make_run(
            runs_root,
            "task1",
            "20260219T100000Z-active",
            status="active",
            writer=writer,
        )
        _make_run(
            runs_root,
            "task1",
            "20260220T100000Z-done",
            status="complete",
            writer=writer,
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
        assert result == active_run

    def test_failed_run_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        active_run = _make_run(
            runs_root,
            "task1",
            "20260219T100000Z-active",
            status="active",
            writer=writer,
        )
        _make_run(
            runs_root,
            "task1",
            "20260220T100000Z-fail",
            status="failed",
            writer=writer,
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
        assert result == active_run

    def test_all_completed_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        writer: FileStateWriter,
    ) -> None:
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        _make_run(
            runs_root,
            "task1",
            "20260219T100000Z-done1",
            status="complete",
            writer=writer,
        )
        _make_run(
            runs_root,
            "task1",
            "20260220T100000Z-done2",
            status="complete",
            writer=writer,
        )

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
        assert result is None

    def test_legacy_run_without_status_treated_as_active(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Backward compat: runs without status field are active."""
        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run_dir = runs_root / "task1" / "20260219T100000Z-legacy"
        (run_dir / "meta").mkdir(parents=True)
        # No status field at all
        (run_dir / "meta" / "run.yaml").write_text("run_id: legacy\n")

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        result = find_run_via_mtime_scan()
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
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        from trw_mcp.models.run import RunState
        from trw_mcp.state.persistence import FileStateReader, model_to_dict

        state = RunState(run_id="test", task="test", owner_session_id="abc123")
        yaml_path = tmp_path / "run.yaml"
        writer.write_yaml(yaml_path, model_to_dict(state))

        reader = FileStateReader()
        data = reader.read_yaml(yaml_path)
        assert data["owner_session_id"] == "abc123"


class TestMarkRunComplete:
    """FR01: trw_deliver marks run as complete."""

    def test_mark_run_complete(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools.ceremony import _mark_run_complete

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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        writer: FileStateWriter,
    ) -> None:
        """After marking complete, find_active_run skips the run."""
        from trw_mcp.tools.ceremony import _mark_run_complete

        project = tmp_path / "project"
        runs_root = project / ".trw" / "runs"
        run = _make_run(runs_root, "task1", "20260220T100000Z-test", writer=writer)

        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        assert find_run_via_mtime_scan() == run

        _mark_run_complete(run)
        assert find_run_via_mtime_scan() is None


class TestOwnershipWarning:
    """FR05: Ownership-aware recovery warnings."""

    def test_get_run_status_includes_owner(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
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
        self,
        tmp_path: Path,
        writer: FileStateWriter,
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


# ---------------------------------------------------------------------------
# PRD-CORE-219-FR01: content-bound ownership manifest
# ---------------------------------------------------------------------------


def test_prd_core_219_fr01(tmp_path: Path) -> None:
    """FR01 acceptance: Given unowned, overlapping, traversal, escape, or
    changed-since-claim paths, When ownership validates, Then preparation fails
    and worktree, shared index, HEAD, and refs remain semantically unchanged."""
    import hashlib
    import subprocess

    from trw_mcp.models.git_commit_transaction import OwnershipManifest
    from trw_mcp.state.git_commit_transaction import snapshot_shared_state, validate_ownership

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    owned = repo / "src" / "owned.py"
    owned.parent.mkdir()
    owned.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    parent = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    digest = "sha256:" + hashlib.sha256(owned.read_bytes()).hexdigest()

    def manifest(paths: tuple[str, ...], digests: dict[str, str]) -> OwnershipManifest:
        return OwnershipManifest(
            transaction_id="txn-1",
            run_id="run-1",
            parent_oid=parent,
            owned_paths=paths,
            path_digests=digests,
        )

    # Valid claim: no failures.
    assert validate_ownership(manifest(("src/owned.py",), {"src/owned.py": digest}), repo) == []
    # Unowned (no content binding).
    assert any("unowned" in f for f in validate_ownership(manifest(("src/owned.py",), {}), repo))
    # Traversal and absolute paths.
    assert any(
        "traversal" in f for f in validate_ownership(manifest(("../etc/passwd",), {"../etc/passwd": digest}), repo)
    )
    assert any("traversal" in f for f in validate_ownership(manifest(("/etc/passwd",), {"/etc/passwd": digest}), repo))
    # Escape via symlink.
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")
    link = repo / "src" / "link.py"
    link.symlink_to(outside)
    assert any("escapes" in f for f in validate_ownership(manifest(("src/link.py",), {"src/link.py": digest}), repo))
    # Overlap with another transaction's claim.
    other = manifest(("src/owned.py",), {"src/owned.py": digest})
    assert any(
        "overlaps" in f
        for f in validate_ownership(manifest(("src/owned.py",), {"src/owned.py": digest}), repo, other_claims=(other,))
    )
    # Changed since claim.
    owned.write_text("v2\n", encoding="utf-8")
    before = snapshot_shared_state(repo)
    assert any(
        "changed since claim" in f
        for f in validate_ownership(manifest(("src/owned.py",), {"src/owned.py": digest}), repo)
    )

    # Read-only guarantee: validation changed nothing (worktree, index, HEAD, refs).
    assert snapshot_shared_state(repo) == before


def test_prd_core_219_nfr02(tmp_path) -> None:
    """NFR02 acceptance: under foreign staged AND unstaged noise, the
    candidate's tree delta is exactly the owned paths — no unowned byte can
    enter a candidate through the production path."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    for name in ("owned.py", "foreign_a.py", "foreign_b.py"):
        (repo / name).write_text(f"{name}-v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    # Foreign noise: one staged, one unstaged, one untracked.
    (repo / "foreign_a.py").write_text("staged-noise\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "foreign_a.py"], check=True, capture_output=True)
    (repo / "foreign_b.py").write_text("unstaged-noise\n", encoding="utf-8")
    (repo / "untracked.py").write_text("untracked-noise\n", encoding="utf-8")
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")

    from tests._git_commit_workflow_support import verified_candidate

    result, _run_dir = verified_candidate(repo, ("owned.py",), "feat: owned\n", "run/nfr02")
    delta = subprocess.run(
        ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only", "-r", result["candidate_oid"]],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert delta == ["owned.py"]  # strictly the owned claim, nothing foreign
    blob = subprocess.run(
        ["git", "-C", str(repo), "show", f"{result['candidate_oid']}:foreign_a.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert blob == "foreign_a.py-v1\n"  # foreign staged noise NOT absorbed
