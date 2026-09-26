"""Repeatedly replacing a pinned-read target must not grow the process's open-fd count (P1 rc9).

Before the fix, ``trw_mcp._pinned_read`` cached one descriptor per inode
forever: replacing a status database's file (a new inode each time) and
reading its header pinned a new descriptor every call, eventually exhausting
the process's file-descriptor table (EMFILE) and taking down unrelated MCP
work. The fix retires a path's previously-pinned descriptor once that path
resolves to a different inode -- see ``_pinned_read``'s module docstring for
why that close can never drop a live lock.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from trw_mcp import _pinned_read


@pytest.fixture(autouse=True)
def _clean_pinned_read_state() -> None:
    """Isolate the module-level fd cache between tests (it is process-global by design)."""
    for fd in _pinned_read._fds.values():
        try:
            os.close(fd)
        except OSError:  # trw-fail-silent-allow: best-effort teardown close; a double-close is harmless to isolate
            pass
    _pinned_read._fds.clear()
    _pinned_read._path_inode.clear()
    yield
    for fd in _pinned_read._fds.values():
        try:
            os.close(fd)
        except OSError:  # trw-fail-silent-allow: best-effort teardown close; a double-close is harmless to isolate
            pass
    _pinned_read._fds.clear()
    _pinned_read._path_inode.clear()


def _replace_with_new_inode(path: Path, content: bytes) -> None:
    """Atomically swap *path* for a brand-new inode (like a store reset/rewrite would)."""
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def test_repeated_inode_replacement_retires_the_stale_descriptor(tmp_path: Path) -> None:
    """The discriminating case: this failed against the pre-fix cache, which never retired."""
    target = tmp_path / "operations.sqlite3"
    target.write_bytes(b"0" * 32)

    for i in range(40):
        _replace_with_new_inode(target, str(i).encode().ljust(32, b"0"))
        _pinned_read.read_at(target, 32)
        # At most one descriptor is ever pinned for a single, repeatedly-replaced path.
        assert len(_pinned_read._fds) == 1, (
            f"iteration {i}: expected exactly 1 pinned fd for one churned path, "
            f"got {len(_pinned_read._fds)} -- stale descriptors are leaking"
        )


def test_repeated_inode_replacement_does_not_grow_process_fd_count(tmp_path: Path) -> None:
    """Same scenario, verified against the OS's real fd table rather than the cache's own bookkeeping."""
    target = tmp_path / "operations.sqlite3"
    target.write_bytes(b"0" * 32)
    _pinned_read.read_at(target, 32)
    baseline = len(os.listdir("/dev/fd"))

    for i in range(40):
        _replace_with_new_inode(target, str(i).encode().ljust(32, b"0"))
        _pinned_read.read_at(target, 32)

    assert len(os.listdir("/dev/fd")) <= baseline + 1


def test_same_inode_rewrite_never_retires(tmp_path: Path) -> None:
    """A write-in-place (no replacement) keeps tracking the live file, unlike a real replace."""
    target = tmp_path / "operations.sqlite3"
    target.write_bytes(b"a" * 32)
    first = _pinned_read.read_at(target, 32)
    assert first == b"a" * 32

    with target.open("r+b") as handle:
        handle.write(b"b" * 32)

    second = _pinned_read.read_at(target, 32)
    assert second == b"b" * 32
    assert len(_pinned_read._fds) == 1


def test_distinct_paths_each_get_their_own_pinned_descriptor(tmp_path: Path) -> None:
    a = tmp_path / "a.sqlite3"
    b = tmp_path / "b.sqlite3"
    a.write_bytes(b"a" * 16)
    b.write_bytes(b"b" * 16)

    _pinned_read.read_at(a, 16)
    _pinned_read.read_at(b, 16)

    assert len(_pinned_read._fds) == 2


def test_capacity_cap_raises_instead_of_growing_without_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pinned_read, "_MAX_PINNED_FDS", 2)
    paths = [tmp_path / f"db-{i}.sqlite3" for i in range(3)]
    for path in paths[:2]:
        path.write_bytes(b"x" * 8)
        _pinned_read.read_at(path, 8)

    paths[2].write_bytes(b"x" * 8)
    with pytest.raises(_pinned_read.PinnedReadCapacityExceeded):
        _pinned_read.read_at(paths[2], 8)
    assert len(_pinned_read._fds) == 2


def test_concurrent_retire_and_read_never_ebadf_or_cross_file_bytes(tmp_path: Path) -> None:
    """The discriminating case for the C12 race (P1 rc9).

    Thread A hammers ``read_at`` on a fixed path while thread B repeatedly
    replaces that path with a brand-new inode (each generation carrying a
    distinct marker) and also reads it. Before the fix, ``_pinned_fd`` handed
    out an fd and released the lock *before* ``os.pread`` ran, so a retire on
    thread B could close that exact fd while thread A's ``pread`` was in
    flight -- either an ``OSError`` (EBADF) or, if the closed fd number was
    reused by thread B's concurrent ``os.open``, bytes belonging to a
    different generation's file than the one the read call names. Every
    successful read here must return exactly one generation's 32-byte marker,
    never a mix, and no thread may ever see EBADF.
    """
    target = tmp_path / "mailbox.sqlite3"
    target.write_bytes(b"g".ljust(32, b"0"))

    iterations = 400
    errors: list[BaseException] = []
    bad_reads: list[bytes] = []
    stop = threading.Event()

    def _marker(generation: int) -> bytes:
        return f"g{generation}".encode().ljust(32, b"0")

    valid_markers = {_marker(i) for i in range(iterations + 1)} | {b"g".ljust(32, b"0")}

    def _replace_with_new_inode(path: Path, content: bytes) -> None:
        tmp = path.with_suffix(path.suffix + f".new{threading.get_ident()}")
        tmp.write_bytes(content)
        os.replace(tmp, path)

    def _reader() -> None:
        while not stop.is_set():
            try:
                data = _pinned_read.read_at(target, 32)
            except OSError as exc:  # EBADF (or any other close-underneath failure) is the bug
                errors.append(exc)
                return
            if data not in valid_markers:
                bad_reads.append(data)

    def _replacer() -> None:
        for i in range(iterations):
            try:
                _replace_with_new_inode(target, _marker(i))
                _pinned_read.read_at(target, 32)
            except OSError as exc:
                errors.append(exc)
                return
        stop.set()

    reader_threads = [threading.Thread(target=_reader) for _ in range(3)]
    replacer_thread = threading.Thread(target=_replacer)

    for t in reader_threads:
        t.start()
    replacer_thread.start()

    replacer_thread.join(timeout=30)
    stop.set()
    for t in reader_threads:
        t.join(timeout=30)

    assert not errors, f"reader/replacer hit OSError under concurrent retire+read: {errors!r}"
    assert not bad_reads, f"read returned bytes not matching any known generation marker: {bad_reads!r}"
