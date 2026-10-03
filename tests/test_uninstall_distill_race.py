"""Uninstall coordinates with the detached post-commit writer instead of racing it (UNINSTALL-DISTILL-RACE).

The post-commit worker rebuilds ``.trw/distill/map-cache`` while it holds an flock on
``.trw/runtime/post-commit.lock``. An uninstall walk that overlapped it saw ENOTEMPTY and exited 1. A fake
writer stands in for the worker: it holds the real lock and writes a file mid-walk, deterministically.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from trw_mcp.server import _uninstall_corpus, _uninstall_quiesce
from trw_mcp.server._uninstall_corpus import remove_trw_dir

fcntl = pytest.importorskip("fcntl")  # POSIX flock: no writer lock exists elsewhere

pytestmark = pytest.mark.integration


def _display(path: Path, _target: Path) -> str:
    return str(path)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    trw = tmp_path / ".trw"
    (trw / "runtime").mkdir(parents=True)
    (trw / "distill" / "map-cache").mkdir(parents=True)
    (trw / "distill" / "map-cache" / "map.json").write_text("{}", encoding="utf-8")
    return tmp_path, trw


def test_an_enotempty_from_a_writer_mid_walk_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, trw = _project(tmp_path)
    real_rmtree, calls = shutil.rmtree, []

    def writer_then_walk(name: str, *, dir_fd: int) -> None:
        calls.append(name)
        if len(calls) == 1:  # the writer drops a file after the walk listed the directory
            (trw / "distill" / "map-cache" / "late.json").write_text("{}", encoding="utf-8")
            raise OSError(errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY))
        real_rmtree(name, dir_fd=dir_fd)

    monkeypatch.setattr(_uninstall_corpus.shutil, "rmtree", writer_then_walk)

    removed, errors = remove_trw_dir(trw, target, _display)

    assert (removed, errors) == (1, 0)
    assert not trw.exists()


def test_uninstall_waits_for_a_live_post_commit_worker_before_removing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, trw = _project(tmp_path)
    lock_path = trw / "runtime" / "post-commit.lock"
    writer_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(writer_fd, fcntl.LOCK_EX)  # the worker is alive and holds the lock
    waits: list[float] = []

    def worker_finishes(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:  # uninstall is waiting on the lock: now the worker writes its last file and exits
            (trw / "distill" / "map-cache" / "last.json").write_text("{}", encoding="utf-8")
            fcntl.flock(writer_fd, fcntl.LOCK_UN)
            os.close(writer_fd)

    monkeypatch.setattr(_uninstall_quiesce.time, "sleep", worker_finishes)

    removed, errors = remove_trw_dir(trw, target, _display)

    assert waits, "uninstall did not wait for the live holder"
    assert (removed, errors) == (1, 0)
    assert not trw.exists(), "the writer's last file would have been left behind"


def test_a_holder_that_outlives_the_wait_keeps_trw_and_names_the_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A live writer is never ignored: .trw stays, the exit is non-zero, and the message says who holds it."""
    target, trw = _project(tmp_path)
    lock = trw / "runtime" / "post-commit.lock"
    writer_fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(writer_fd, fcntl.LOCK_EX)
    os.write(writer_fd, b'{"pid": 4242, "head_sha": "abc"}')
    monkeypatch.setattr(_uninstall_quiesce, "WAIT_SECONDS", 0.0)
    try:
        removed, errors = remove_trw_dir(trw, target, _display)
    finally:
        os.close(writer_fd)

    out = capsys.readouterr().out
    assert (removed, errors) == (0, 1)
    assert (trw / "distill" / "map-cache" / "map.json").is_file(), "nothing was removed while a writer is alive"
    assert "runtime/post-commit.lock" in out and "pid 4242" in out and "left in place" in out, out


def test_an_absent_lock_is_created_and_held_so_a_later_writer_defers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that starts mid-removal finds the lock held and defers instead of recreating .trw/runtime."""
    from trw_mcp.tools._post_commit import _acquire_lock

    target, trw = _project(tmp_path)
    (trw / "state").mkdir()
    (trw / "state" / "x.json").write_text("{}", encoding="utf-8")
    assert not (trw / "runtime" / "post-commit.lock").exists(), "the lock starts absent"
    real_rmtree, writers = shutil.rmtree, []

    def a_commit_lands_mid_removal(name: str, *, dir_fd: int) -> None:
        if not writers:  # the first directory removed is not a writer directory: the lock must already be held
            writers.append(_acquire_lock(trw / "runtime" / "post-commit.lock", "deadbeef"))
        real_rmtree(name, dir_fd=dir_fd)

    monkeypatch.setattr(_uninstall_corpus.shutil, "rmtree", a_commit_lands_mid_removal)

    assert remove_trw_dir(trw, target, _display) == (1, 0)
    assert writers == [(None, -1)], f"the late writer got the lock: {writers}"


def test_the_writer_directories_are_removed_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, trw = _project(tmp_path)
    (trw / "aaa").mkdir()
    (trw / "zzz").mkdir()
    real_rmtree, order = shutil.rmtree, []

    def record(name: str, *, dir_fd: int) -> None:
        order.append(name)
        real_rmtree(name, dir_fd=dir_fd)

    monkeypatch.setattr(_uninstall_corpus.shutil, "rmtree", record)

    remove_trw_dir(trw, target, _display)

    assert order[-2:] == ["distill", "runtime"] and order[:2] == ["aaa", "zzz"], order


def test_no_live_holder_means_no_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, trw = _project(tmp_path)

    def must_not_sleep(_seconds: float) -> None:
        raise AssertionError("uninstall waited with no writer running")

    monkeypatch.setattr(_uninstall_quiesce.time, "sleep", must_not_sleep)

    assert remove_trw_dir(trw, target, _display) == (1, 0)


def test_a_lock_the_holder_unlinked_on_release_is_not_mistaken_for_the_live_one(tmp_path: Path) -> None:
    """post-commit.lock is unlinked by its holder on release: a flock on the retired inode proves nothing."""
    target, trw = _project(tmp_path)
    lock = trw / "runtime" / "post-commit.lock"
    lock.write_text("", encoding="utf-8")
    trw_fd = os.open(trw, os.O_RDONLY | os.O_DIRECTORY)
    try:
        real_open, opened = os.open, []

        def swap_the_inode_once(path: str, flags: int, *args: object, **kwargs: object) -> int:
            fd = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
            if path == "post-commit.lock" and not opened:
                opened.append(fd)
                lock.unlink()  # the previous holder released: our descriptor now names a retired inode
                lock.write_text("", encoding="utf-8")  # a new worker created a fresh lock file at the path
            return fd

        import unittest.mock as mock

        with mock.patch.object(_uninstall_quiesce.os, "open", swap_the_inode_once):
            held = _uninstall_quiesce._take(trw_fd, Path("runtime") / "post-commit.lock", 0.0)
        assert held is not None
        assert os.fstat(held).st_ino == lock.stat().st_ino, "locked the retired inode, not the file at the path"
        os.close(held)
    finally:
        os.close(trw_fd)


def _late_writer_after(monkeypatch: pytest.MonkeyPatch, trw: Path, directory: str, writers: list[object]) -> None:
    """After the walk removes *directory*, a commit's worker recreates .trw/runtime with the real lock code."""
    from trw_mcp.tools._post_commit import _acquire_lock

    real_rmtree = shutil.rmtree

    def remove_then_write(name: str, *, dir_fd: int) -> None:
        real_rmtree(name, dir_fd=dir_fd)
        if name == directory:
            writers.append(_acquire_lock(trw / "runtime" / "post-commit.lock", "deadbeef"))

    monkeypatch.setattr(_uninstall_corpus.shutil, "rmtree", remove_then_write)


def test_a_writer_recreating_runtime_after_the_walk_is_an_error_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The residual race: .trw survives with the recreated directory, so uninstall must say so and exit non-zero."""
    target, trw = _project(tmp_path)
    writers: list[object] = []
    _late_writer_after(monkeypatch, trw, "runtime", writers)

    removed, errors = remove_trw_dir(trw, target, _display)

    out = capsys.readouterr().out
    assert (trw / "runtime").is_dir(), "the late writer did recreate .trw/runtime"
    assert (removed, errors) == (0, 1), "a surviving .trw was reported as success"
    assert "runtime" in out and "run uninstall again" in out, out
    for _state, fd in [w for w in writers if isinstance(w, tuple) and w[1] >= 0]:
        os.close(fd)


def test_without_flock_a_surviving_trw_is_still_reported_as_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Windows has no flock, so no writer lock is held: the final leftover check is the only guard."""
    target, trw = _project(tmp_path)
    monkeypatch.setattr(_uninstall_quiesce, "fcntl", None)
    writers: list[object] = []
    _late_writer_after(monkeypatch, trw, "runtime", writers)

    removed, errors = remove_trw_dir(trw, target, _display)

    out = capsys.readouterr().out
    assert (removed, errors) == (0, 1) and "run uninstall again" in out, out
    for _state, fd in [w for w in writers if isinstance(w, tuple) and w[1] >= 0]:
        os.close(fd)


def test_a_clean_uninstall_still_reports_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target, trw = _project(tmp_path)

    assert remove_trw_dir(trw, target, _display) == (1, 0)
    assert "run uninstall again" not in capsys.readouterr().out


def test_a_lock_that_keeps_getting_retired_gives_up_instead_of_spinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNINSTALL-QUIESCE-KIS (2): the retired-inode retry did not count against the bounded wait, so a path whose inode changed on every look looped forever."""
    target, trw = _project(tmp_path)
    (trw / "runtime" / "post-commit.lock").write_text("", encoding="utf-8")
    trw_fd = os.open(trw, os.O_RDONLY | os.O_DIRECTORY)
    looks: list[str] = []

    class OsWithMovingInode:
        """The real os, except that the lock path names a different inode on every look (the module's own ``os`` only: pytest keeps the real one)."""

        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

        @staticmethod
        def stat(path: str, *, dir_fd: int) -> os.stat_result:
            looks.append(path)
            if len(looks) > 10_000:
                raise AssertionError("still retrying after 10,000 looks: the retry is unbounded")
            return os.stat_result((0, 100_000 + len(looks), 0, 1, 0, 0, 0, 0, 0, 0))

    monkeypatch.setattr(_uninstall_quiesce, "os", OsWithMovingInode())
    try:
        with pytest.raises(
            _uninstall_quiesce.WriterStillRunning, match="replaced"
        ):  # reported like a holder that outlived the wait, never chased
            _uninstall_quiesce._take(trw_fd, Path("runtime") / "post-commit.lock", 10.0)
    finally:
        os.close(trw_fd)
    assert len(looks) <= _uninstall_quiesce.MAX_RETIRED_RETRIES
