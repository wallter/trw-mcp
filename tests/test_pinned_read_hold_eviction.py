"""SERIAL-RUN-LEAKS (C): at the cap, the least recently read pinned descriptor that nothing HOLDS is evicted.

B71-08 as amended (lead, 2026-10-03): never evict a HELD pinned fd. A hold is an open connection opened through
``held_connect``. These cases pin the eviction rule; the decisive one is the cross-process lock check: a
connection mid-transaction keeps its lock however many other files churn through the cache.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests._checkout_access_state import holding, reset_pinned_reads
from trw_mcp import _checkout_access
from trw_mcp._checkout_access import held_connect, read_at
from trw_mcp.tools._delivery_journal_store import JournalStore

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Windows reads bypass the pinned-fd cache (read_at opens per call)"
)


@pytest.fixture(autouse=True)
def _small_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_pinned_reads()
    monkeypatch.setattr(_checkout_access, "_MAX_PINNED_FDS", 3)
    yield
    reset_pinned_reads()


def _file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"data" + name.encode())
    return path


def _pinned(path: Path) -> bool:
    key = _checkout_access._path_inode.get(path)
    return key is not None and key in _checkout_access._fds


def test_the_least_recently_read_idle_descriptor_is_the_one_evicted(tmp_path: Path) -> None:
    a, b, c, d = (_file(tmp_path, n) for n in "abcd")
    for path in (a, b, c):
        read_at(path, 4)
    read_at(a, 4)  # a is now the most recent; b the least

    assert read_at(d, 4) == b"data"

    assert not _pinned(b) and _pinned(a) and _pinned(c) and _pinned(d)


def test_a_held_descriptor_is_skipped_for_the_next_least_recent(tmp_path: Path) -> None:
    a, b, c, d = (_file(tmp_path, n) for n in "abcd")
    for path in (a, b, c):
        read_at(path, 4)

    with holding(a):
        read_at(d, 4)

    assert _pinned(a) and not _pinned(b)


def test_a_descriptor_with_a_read_in_flight_is_never_closed(tmp_path: Path) -> None:
    a, b, c, d = (_file(tmp_path, n) for n in "abcd")
    for path in (a, b, c):
        read_at(path, 4)
    key_a = _checkout_access._path_inode[a]
    lock = _checkout_access._inode_locks[key_a]

    with lock:  # what read_at holds across its pread
        read_at(d, 4)

    assert key_a in _checkout_access._fds


def test_every_descriptor_held_still_refuses(tmp_path: Path) -> None:
    pins = [_file(tmp_path, n) for n in "abc"]
    for path in pins:
        read_at(path, 4)

    with holding(*pins), pytest.raises(_checkout_access.PinnedReadCapacityExceeded):
        read_at(_file(tmp_path, "d"), 4)


def _released() -> None:
    with _checkout_access._map_lock:
        _checkout_access._drain_releases_locked()


def test_a_hold_lasts_until_the_connection_is_destroyed_not_merely_closed(tmp_path: Path) -> None:
    """codex r1: an unfinished cursor keeps SQLite's native connection and read lock after a Python close()."""
    path = _file(tmp_path, "x.sqlite3")
    key = str(Path(path).resolve())
    inode = (path.stat().st_dev, path.stat().st_ino)

    conn = held_connect(path)
    conn.close()
    _released()
    assert _checkout_access._holds.get(key) == 1, "closed but not destroyed: still held"

    del conn
    gc.collect()
    _released()
    # Only this connection's own hold: tests that inject their own Connection factory keep theirs (no __del__ hook).
    assert key not in _checkout_access._holds and inode not in _checkout_access._held_inodes


def test_a_native_close_that_fails_keeps_the_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """codex r1: if the native close fails the connection may still hold its locks, so the hold is kept for good."""
    path = _file(tmp_path, "x.sqlite3")
    key = str(Path(path).resolve())
    conn = held_connect(path)

    def refuse(_conn: object) -> None:
        raise _checkout_access.sqlite3.ProgrammingError("close refused")

    monkeypatch.setattr(_checkout_access, "_native_close", refuse)
    del conn
    gc.collect()
    _released()

    assert _checkout_access._holds.get(key) == 1


def test_a_connection_destroyed_by_a_gc_under_the_cache_lock_does_not_deadlock(tmp_path: Path) -> None:
    """codex r1: a finalizer that took ``_map_lock`` deadlocked when a GC ran inside the eviction's critical section."""
    path = _file(tmp_path, "x.sqlite3")
    conn = held_connect(path)
    cycle: list[object] = [conn]
    cycle.append(cycle)  # only the cycle collector can free it
    del conn
    finished = threading.Event()

    def collect_under_the_lock() -> None:
        with _checkout_access._map_lock:
            cycle.clear()
            gc.collect()
        finished.set()

    worker = threading.Thread(target=collect_under_the_lock, daemon=True)
    worker.start()
    assert finished.wait(timeout=10), "the GC finalizer deadlocked on _map_lock"
    # Collected on another thread, the native close fails its same-thread check, so the hold is KEPT (conservative):
    # a leaked hold only protects a descriptor longer. The point here is that the collection finished at all.


def test_a_file_replaced_while_a_connection_opens_blocks_every_eviction_until_it_is_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r1: the post-open stat could name the replacement, leaving the inode SQLite really opened unprotected."""
    a, b, c = (_file(tmp_path, n) for n in "abc")
    for path in (a, b, c):
        read_at(path, 4)
    real_connect = _checkout_access.sqlite3.connect

    def connect_then_replace(*args: object, **kwargs: object) -> object:
        conn = real_connect(*args, **kwargs)
        _file(tmp_path, "a.new").replace(a)
        return conn

    monkeypatch.setattr(_checkout_access.sqlite3, "connect", connect_then_replace)
    conn = held_connect(a)
    monkeypatch.setattr(_checkout_access.sqlite3, "connect", real_connect)

    assert _checkout_access._uncertain_holds == 1
    with pytest.raises(_checkout_access.PinnedReadCapacityExceeded):
        read_at(_file(tmp_path, "d"), 4)

    del conn
    gc.collect()
    assert read_at(_file(tmp_path, "e"), 4) == b"data"
    assert _checkout_access._uncertain_holds == 0


def test_replacing_a_file_many_times_keeps_the_use_order_bounded(tmp_path: Path) -> None:
    """codex r1: retirement left every replaced inode in the LRU order."""
    path = _file(tmp_path, "mailbox")
    for n in range(200):
        _file(tmp_path, f"gen{n}").replace(path)
        read_at(path, 4)

    assert len(_checkout_access._use_order) <= len(_checkout_access._fds) + 1


def test_the_inode_a_connection_opened_stays_protected_after_its_path_is_replaced(tmp_path: Path) -> None:
    a, b, c = (_file(tmp_path, n) for n in "abc")
    for path in (a, b, c):
        read_at(path, 4)
    key_a = _checkout_access._path_inode[a]

    with holding(a, b, c):
        _file(tmp_path, "a.new").replace(a)  # the path now names a different inode; the connection keeps the old one
        with pytest.raises(_checkout_access.PinnedReadCapacityExceeded):
            read_at(_file(tmp_path, "d"), 4)

    assert key_a in _checkout_access._fds


_PROBE = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0.2, isolation_level=None)
try:
    conn.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as exc:
    print("LOCKED" if "locked" in str(exc) else f"ERROR {exc}")
else:
    print("ACQUIRED")
"""


def _probe(db: Path) -> str:
    done = subprocess.run([sys.executable, "-c", _PROBE, str(db)], capture_output=True, text=True, timeout=30)
    return done.stdout.strip()


def test_churning_the_cache_never_drops_a_live_transactions_lock(tmp_path: Path) -> None:
    """The C15 hazard this cache exists to prevent: closing a descriptor drops every fcntl lock the process holds."""
    store = JournalStore(tmp_path / "project" / ".trw" / "delivery" / "operations.sqlite3")
    store.connect().close()
    conn = store.connect()
    try:
        with store.immediate(conn):
            store.connect_ro().close()  # pins the journal's header descriptor while the write lock is held
            assert _probe(store.db_path) == "LOCKED", "non-vacuity: another process is locked out"
            for n in range(20):  # far past the cap of 3: every other file churns through eviction
                read_at(_file(tmp_path, f"churn{n}"), 4)
            assert _probe(store.db_path) == "LOCKED", "an eviction closed the journal's fd and dropped the lock"
    finally:
        conn.close()
    assert _probe(store.db_path) == "ACQUIRED"


def test_concurrent_readers_at_the_cap_never_fail(tmp_path: Path) -> None:
    files = [_file(tmp_path, f"f{n}") for n in range(12)]
    errors: list[BaseException] = []

    def reader(offset: int) -> None:
        try:
            for i in range(300):
                path = files[(i + offset) % len(files)]
                assert read_at(path, 4) == b"data"
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=reader, args=(k,)) for k in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []


_EXCLUSIVE_PROBE = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0.2, isolation_level=None)
try:
    conn.execute("BEGIN EXCLUSIVE")
except sqlite3.OperationalError as exc:
    print("LOCKED" if "locked" in str(exc) else f"ERROR {exc}")
else:
    print("ACQUIRED")
"""


def test_an_unfinished_cursor_keeps_its_read_lock_through_cache_churn_after_close(tmp_path: Path) -> None:
    """codex r1 P1: a closed connection with an unfinished cursor still holds SQLite's SHARED lock natively."""
    store = JournalStore(tmp_path / "project" / ".trw" / "delivery" / "operations.sqlite3")
    writer = store.connect()
    writer.execute("CREATE TABLE IF NOT EXISTS big(n)")
    writer.executemany("INSERT INTO big VALUES (?)", [(n,) for n in range(2000)])
    writer.close()
    del writer
    gc.collect()

    reader = store.connect_ro()
    cursor = reader.execute("SELECT n FROM big")
    cursor.fetchone()  # unfinished: the statement keeps its SHARED lock
    reader.close()
    for n in range(20):
        read_at(_file(tmp_path, f"churn{n}"), 4)

    probe = subprocess.run([sys.executable, "-c", _EXCLUSIVE_PROBE, str(store.db_path)], capture_output=True, text=True)
    assert probe.stdout.strip() == "LOCKED", "an eviction dropped the unfinished cursor's read lock"
    del cursor, reader
    gc.collect()


def test_retiring_a_replaced_path_never_closes_a_held_inode(tmp_path: Path) -> None:
    """Auditor P2: retirement (a path now naming a new inode) closed the old inode's fd whatever held it."""
    a = _file(tmp_path, "a")
    read_at(a, 4)
    key_a = _checkout_access._path_inode[a]
    fd_a = _checkout_access._fds[key_a]

    with holding(a):
        _file(tmp_path, "a.new").replace(a)
        assert read_at(a, 9) == b"dataa.new"  # the path re-pins its new inode...
        os.fstat(fd_a)  # ...and the held old inode's descriptor is still open (EBADF if it was closed)
        assert _checkout_access._fds.get(key_a) == fd_a


def test_a_connection_destroyed_on_another_thread_releases_its_hold(tmp_path: Path) -> None:
    """Auditor P2: a cross-thread destroy failed close()'s same-thread check and leaked the hold for good."""
    path = _file(tmp_path, "x.sqlite3")
    key = str(Path(path).resolve())
    box = [held_connect(path)]

    worker = threading.Thread(target=lambda: (box.clear(), gc.collect()))
    worker.start()
    worker.join()
    _released()

    assert key not in _checkout_access._holds
