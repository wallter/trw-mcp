"""PRD-CORE-298 FR01: a write through the daemon never checkpoints the checkout's memory.db.

An unmigrated checkout still holds its old ``.trw/memory/memory.db`` with a WAL
beside it. ``trw-mcp memory migrate`` backs that file up before it moves
anything, so nothing may fold the WAL into it or leave markers beside it first.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state import _wal_triggers
from trw_mcp.state.memory_adapter import store_learning


class _Due:
    due = True
    reason = "forced"
    wal_size_bytes = 1 << 30
    age_seconds = 1e9


def _digest(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir())}


def test_a_daemon_write_leaves_the_unmigrated_checkout_store_byte_identical(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = tmp_path / ".trw" / "memory"
    memory.mkdir(parents=True)
    # An old stdio server's store: WAL mode, rows still in the WAL, connection open.
    conn = sqlite3.connect(memory / "memory.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("row",)] * 200)
    conn.commit()
    assert (memory / "memory.db-wal").stat().st_size > 0
    # Every checkpoint trigger due, so any checkpoint path that still exists runs.
    monkeypatch.setattr(_wal_triggers, "evaluate_wal_trigger", lambda *a, **k: _Due())
    before = _digest(memory)
    try:
        store_learning(tmp_path / ".trw", "L-walfree", "a daemon write", "detail")
        assert _digest(memory) == before
    finally:
        conn.close()
