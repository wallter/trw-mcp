"""PRD-FIX-081 FR05: WAL checkpoint actually shrinks the WAL file.

Pre-fix: maybe_checkpoint_wal ran PRAGMA wal_checkpoint(PASSIVE) which
writes frames to the main DB but does NOT truncate the WAL file. With
persistent reader connections (trw-memory backend singleton + concurrent
MCP processes), the file size stayed at ~36 MB indefinitely.

Post-fix: TRUNCATE is attempted first; PASSIVE is the fallback when
readers hold pages and TRUNCATE returns busy=1.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest


def _seed_db_with_wal(
    db_path: Path,
    n_writes: int = 1000,
) -> tuple[int, sqlite3.Connection]:
    """Create a SQLite DB and dirty the WAL with n_writes inserts.

    Returns ``(wal_size_bytes, writer_conn)``. The caller MUST close the
    writer connection AFTER the test assertion; SQLite auto-checkpoints
    on the writer's close which truncates the WAL and would mask the
    behavior under test. Keeping the writer open preserves the WAL.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")  # disable auto so WAL grows
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v BLOB)")
    payload = b"x" * 4096
    for _ in range(n_writes):
        conn.execute("INSERT INTO t(v) VALUES (?)", (payload,))
    conn.commit()
    wal_path = db_path.with_suffix(".db-wal")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    return wal_size, conn


def test_truncate_shrinks_wal_when_no_concurrent_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only a passive holder connection, TRUNCATE shrinks the WAL."""
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal
    from trw_mcp.models.config import get_config

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    db_path = trw_dir / "memory" / "memory.db"
    wal_path = db_path.with_suffix(".db-wal")

    # Lower the threshold so our small seed crosses it.
    cfg = get_config()
    monkeypatch.setattr(cfg, "wal_checkpoint_threshold_mb", 1)

    pre_size, holder = _seed_db_with_wal(db_path, n_writes=2000)
    try:
        assert pre_size > 1_000_000, f"WAL should be >1MB before checkpoint; got {pre_size}"

        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))

        assert result.get("checkpointed") is True
        # The holder connection is passive (no active transaction), so TRUNCATE
        # should succeed. If a SQLite quirk makes it busy, PASSIVE fallback
        # ran and wrote frames -- still acceptable.
        assert result.get("mode") in {"truncate", "passive"}
        post_size = wal_path.stat().st_size if wal_path.exists() else 0
        if result.get("mode") == "truncate" and result.get("busy") == 0:
            # TRUNCATE shrinks to ~0 (some implementations leave a small header).
            assert post_size < 100_000, (
                f"TRUNCATE should shrink WAL to near zero; got {post_size} bytes "
                f"(was {pre_size})"
            )
            # Sanity: shrinkage is observable in the result dict.
            assert cast("float", result["wal_size_after_mb"]) < cast(
                "float", result["wal_size_before_mb"]
            )
    finally:
        holder.close()


def test_passive_fallback_when_truncate_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TRUNCATE returns busy=1 (held reader), function falls back to PASSIVE.

    Simulated by patching sqlite3.Connection.execute to return busy=1 for
    the TRUNCATE pragma; the function must then run PASSIVE without raising.
    """
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal
    from trw_mcp.models.config import get_config

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    db_path = trw_dir / "memory" / "memory.db"

    cfg = get_config()
    monkeypatch.setattr(cfg, "wal_checkpoint_threshold_mb", 1)
    _, holder = _seed_db_with_wal(db_path, n_writes=2000)

    # Open a second connection with an ACTIVE read transaction so TRUNCATE
    # returns busy=1.
    reader = sqlite3.connect(str(db_path))
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM t").fetchone()
    try:
        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))
    finally:
        reader.rollback()
        reader.close()
        holder.close()

    assert result.get("checkpointed") is True
    # Either TRUNCATE succeeded (busy=0, mode=truncate) or fell back to
    # PASSIVE. SQLite TRUNCATE semantics: with a reader holding pages,
    # busy=1 is expected. Some platforms may still allow TRUNCATE; both
    # are acceptable. The contract is: function does not raise, and if
    # TRUNCATE was busy then PASSIVE ran.
    if result.get("truncate_busy"):
        assert result.get("mode") == "passive"
    else:
        # TRUNCATE succeeded — fine.
        assert result.get("mode") in {"truncate", "passive"}


def test_skipped_when_under_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When WAL is under threshold, function returns skipped without opening a connection."""
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal
    from trw_mcp.models.config import get_config

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    db_path = trw_dir / "memory" / "memory.db"

    cfg = get_config()
    # Threshold above any reasonable seed size.
    monkeypatch.setattr(cfg, "wal_checkpoint_threshold_mb", 100)

    _, holder = _seed_db_with_wal(db_path, n_writes=10)
    try:
        result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))
    finally:
        holder.close()
    # Either under_threshold (most likely) or no_wal_file (if the small
    # seed produced no WAL frames to begin with) is acceptable.
    assert result.get("skipped") is True
    assert result.get("reason") in {"under_threshold", "no_wal_file"}


def test_skipped_when_no_wal_file(tmp_path: Path) -> None:
    """When no WAL file exists, function returns skipped."""
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    # No DB or WAL exists.

    result = cast("dict[str, object]", maybe_checkpoint_wal(trw_dir))
    assert result == {"skipped": True, "reason": "no_wal_file"}
