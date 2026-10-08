"""Round 2 regressions for update snapshot locks and headless installer consent."""

import errno
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _client_adoption, _refused_restore, _snapshot_fd, _update_transaction

pytestmark = pytest.mark.unit


def test_adoption_probe_releases_nested_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._client_integrations import CLIENT_INTEGRATIONS

    integration = next(i for i in CLIENT_INTEGRATIONS if i.name == "cursor")
    target = tmp_path / "target"
    target.mkdir()
    held: list[Path] = []
    original = _update_transaction.new_snapshot_dir

    def capture(project: Path | None = None) -> Path:
        snap = original(project)
        held.append(snap)
        return snap

    monkeypatch.setattr(_update_transaction, "new_snapshot_dir", capture)
    monkeypatch.setattr(_client_adoption, "_run_writers", lambda *_a, **_k: None)
    _client_adoption.writer_overwrites(target, integration.platform_ids[0], {})
    assert len(held) == 2
    assert all(str(snapshot) not in _snapshot_fd._HELD for snapshot in held)


def test_new_snapshot_cleans_directory_when_flock_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snap = tmp_path / "created"
    monkeypatch.setattr(_refused_restore, "_snapshots_dir", lambda *, create: tmp_path)
    monkeypatch.setattr(_refused_restore.tempfile, "mkdtemp", lambda **_kw: (snap.mkdir(), str(snap))[1])

    def fail_flock(*_args: object) -> None:
        raise OSError(errno.ENOLCK, "unsupported flock")

    monkeypatch.setattr(_snapshot_fd.fcntl, "flock", fail_flock)
    held_before = dict(_snapshot_fd._HELD)  # other tests in this worker may hold their own snapshots
    with pytest.raises(OSError, match="unsupported flock"):
        _refused_restore.new_snapshot_dir(tmp_path / "project")
    assert not snap.exists()
    assert _snapshot_fd._HELD == held_before, "a failed flock must not leave a held snapshot behind"


def test_hold_closes_fd_when_flock_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snap = tmp_path / "snap"
    snap.mkdir()
    closed: list[int] = []
    close = _snapshot_fd.os.close
    monkeypatch.setattr(_snapshot_fd.os, "close", lambda fd: (closed.append(fd), close(fd))[1])
    monkeypatch.setattr(
        _snapshot_fd.fcntl, "flock", lambda *_a: (_ for _ in ()).throw(OSError(errno.ENOLCK, "no flock"))
    )
    held_before = dict(_snapshot_fd._HELD)  # other tests in this worker may hold their own snapshots
    with pytest.raises(OSError, match="no flock"):
        _snapshot_fd._hold(snap)
    assert len(closed) == 1
    assert _snapshot_fd._HELD == held_before, "a failed flock must not leave a held snapshot behind"


def test_run_in_scratch_uses_marker_without_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    target.mkdir()
    monkeypatch.setattr(
        _update_transaction.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("git")),
    )
    seen: list[Path] = []

    def apply(scratch: Path) -> None:
        assert (scratch / ".git").is_dir()
        seen.append(scratch)

    _update_transaction.run_in_scratch(target, {"errors": []}, apply)
    assert len(seen) == 1
    assert not seen[0].exists()


def test_run_in_scratch_removes_tree_when_lock_release_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    target.mkdir()
    seen: list[Path] = []
    monkeypatch.setattr(_update_transaction.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 1))
    monkeypatch.setattr(
        _update_transaction, "release_snapshot", lambda _path: (_ for _ in ()).throw(OSError("close failed"))
    )
    with pytest.raises(OSError, match="close failed"):
        _update_transaction.run_in_scratch(target, {"errors": []}, seen.append)
    assert len(seen) == 1
    assert not seen[0].exists()


def test_snapshot_copy_removes_tree_when_lock_release_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    target.mkdir()
    (target / "managed.txt").write_text("managed", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setattr(_update_transaction, "_validate_transaction_surface", lambda _path: None)
    monkeypatch.setattr(_update_transaction, "_refuse_git_marked_owned_dirs", lambda _path: None)
    monkeypatch.setattr(_update_transaction, "_refuse_special_managed_files", lambda _path: None)
    monkeypatch.setattr(_update_transaction, "_TRANSACTION_DIRS", ())
    monkeypatch.setattr(_update_transaction, "_TRANSACTION_FILES", ("managed.txt",))
    monkeypatch.setattr(_update_transaction, "new_snapshot_dir", lambda _path: snapshot)
    monkeypatch.setattr(
        _update_transaction.shutil, "copy2", lambda *_a, **_k: (_ for _ in ()).throw(OSError("copy failed"))
    )
    monkeypatch.setattr(
        _update_transaction, "release_snapshot", lambda _path: (_ for _ in ()).throw(OSError("close failed"))
    )
    with pytest.raises(OSError, match="close failed"):
        _update_transaction._snapshot_transaction_paths(target)
    assert not snapshot.exists()
