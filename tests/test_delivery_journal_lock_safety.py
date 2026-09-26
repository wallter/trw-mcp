"""A status read must never release a live connection's SQLite locks (C15 class).

POSIX fcntl locks belong to a (process, inode) pair: ``close()`` on ANY
descriptor for the journal drops every lock the process holds on it. The
legacy-WAL header sniff in ``connect_ro`` used to open and close the journal,
so a status call in the MCP server stripped a concurrent writer's
``BEGIN IMMEDIATE`` and a second process could then write alongside it. Each
probe runs in a separate process, the only place the lock is visible.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.tools._delivery_journal_store import JournalStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX advisory-lock semantics")

_WRITE_PROBE = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
try:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("CREATE TABLE IF NOT EXISTS intruder(x)")
    conn.execute("COMMIT")
    print("granted")
except sqlite3.OperationalError:
    print("blocked")
"""


def _other_process_can_write(db: Path) -> bool:
    out = subprocess.run(
        [sys.executable, "-c", _WRITE_PROBE, str(db)], capture_output=True, text=True, timeout=30, check=True
    )
    return out.stdout.strip() == "granted"


def _store(tmp_path: Path) -> JournalStore:
    store = JournalStore(tmp_path / "delivery" / "operations.sqlite3", busy_timeout_ms=200)
    store.connect().close()
    return store


def _status_read(store: JournalStore) -> None:
    ro = store.connect_ro()
    try:
        assert ro.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is not None
    finally:
        ro.close()


def test_probe_sees_a_raw_close_drop_the_writers_lock(tmp_path: Path) -> None:
    """Guards the probe itself: the pre-fix open/close is visible as a granted write."""
    store = _store(tmp_path)
    writer = store.connect()
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert _other_process_can_write(store.db_path) is False
        with store.db_path.open("rb") as handle:
            handle.read(20)
        assert _other_process_can_write(store.db_path) is True
    finally:
        writer.rollback()
        writer.close()


def test_status_read_keeps_a_live_writers_lock_when_first_pinned_under_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    writer = store.connect()
    writer.execute("BEGIN IMMEDIATE")
    try:
        _status_read(store)  # the header descriptor is first opened while the lock is held
        assert _other_process_can_write(store.db_path) is False
        _status_read(store)
        assert _other_process_can_write(store.db_path) is False
    finally:
        writer.rollback()
        writer.close()


def test_status_read_keeps_a_live_writers_lock_when_pinned_before_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _status_read(store)  # the header descriptor is pinned before any lock exists
    writer = store.connect()
    writer.execute("BEGIN IMMEDIATE")
    try:
        _status_read(store)
        assert _other_process_can_write(store.db_path) is False
    finally:
        writer.rollback()
        writer.close()
    assert _other_process_can_write(store.db_path) is True


def test_pinned_header_tracks_an_in_place_wal_migration(tmp_path: Path) -> None:
    """No verdict is cached: connect() rewrites the header on the same inode."""
    db = tmp_path / "delivery" / "operations.sqlite3"
    db.parent.mkdir(parents=True)
    legacy = sqlite3.connect(db)
    legacy.execute("PRAGMA journal_mode=WAL")
    legacy.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    legacy.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    legacy.commit()
    legacy.close()
    store = JournalStore(db, busy_timeout_ms=200)
    inode = db.stat().st_ino

    assert store._uses_legacy_wal_mode() is True
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()
    store.connect().close()
    assert db.stat().st_ino == inode
    assert store._uses_legacy_wal_mode() is False
