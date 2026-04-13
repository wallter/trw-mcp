"""Tests for PRD-INFRA-068 session-start memory health dashboard (C3)."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from trw_mcp.tools._health_dashboard import compute_memory_health


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_trw_dir(tmp_path: Path, create_db: bool = True) -> Path:
    trw = tmp_path / ".trw"
    mem = trw / "memory"
    mem.mkdir(parents=True)
    if create_db:
        conn = sqlite3.connect(str(mem / "memory.db"))
        conn.execute("CREATE TABLE memories (id TEXT, content TEXT)")
        conn.execute("INSERT INTO memories VALUES ('a', 'x')")
        conn.commit()
        conn.close()
    return trw


# ---------------------------------------------------------------------------
# Graceful degradation — all features disabled (sprint exit criterion guard)
# ---------------------------------------------------------------------------


def test_health_dashboard_all_features_disabled(tmp_path: Path) -> None:
    """CRITICAL: with B2/B3/B4 all unused, dashboard reports absence cleanly.

    Regression guard for the sprint exit criterion — proves each field
    gracefully degrades when upstream features haven't emitted state.
    """
    trw = _make_trw_dir(tmp_path)
    health = compute_memory_health(trw)

    assert health["integrity_ok"] is True
    assert health["corrupt_bak_present"] is False
    assert health["corrupt_bak_count"] == 0
    assert health["concurrent_writers"] == 0
    assert health["last_snapshot_age_hours"] is None
    assert health["last_integrity_check_age_minutes"] is None


def test_health_dashboard_empty_trw_dir(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path, create_db=False)
    health = compute_memory_health(trw)
    # Missing DB is NOT a regression.
    assert health.get("integrity_ok") is True
    assert health.get("corrupt_bak_present") is False


# ---------------------------------------------------------------------------
# Corrupt backup detection
# ---------------------------------------------------------------------------


def test_health_detects_timestamped_corrupt_bak(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    (trw / "memory" / "memory.db.corrupt.2026-04-12T03-14-59Z.bak").write_bytes(b"x")
    health = compute_memory_health(trw)
    assert health["corrupt_bak_present"] is True
    assert health["corrupt_bak_count"] == 1


def test_health_detects_legacy_corrupt_bak(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    (trw / "memory" / "memory.db.corrupt.bak").write_bytes(b"x")
    (trw / "memory" / "memory.db.corrupt.bak.1").write_bytes(b"x")
    health = compute_memory_health(trw)
    assert health["corrupt_bak_present"] is True
    assert health["corrupt_bak_count"] == 2


def test_health_ignores_unrelated_files(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    (trw / "memory" / "memory.db-wal").write_bytes(b"x")
    (trw / "memory" / "something_else.txt").write_bytes(b"x")
    health = compute_memory_health(trw)
    assert health["corrupt_bak_count"] == 0


# ---------------------------------------------------------------------------
# Snapshot age
# ---------------------------------------------------------------------------


def test_snapshot_age_reports_hours(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    snap_dir = trw / "memory" / "snapshots" / "daily"
    snap_dir.mkdir(parents=True)
    snap = snap_dir / "2026-04-10.db"
    snap.write_bytes(b"x")
    # Push mtime 36 hours into the past.
    past = time.time() - 36 * 3600
    os.utime(str(snap), (past, past))

    health = compute_memory_health(trw)
    # Allow small jitter — 35 or 36 acceptable.
    age = health["last_snapshot_age_hours"]
    assert age is not None
    assert 35 <= age <= 37


def test_snapshot_age_picks_newest(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    snap_dir = trw / "memory" / "snapshots" / "daily"
    snap_dir.mkdir(parents=True)
    (snap_dir / "2026-04-10.db").write_bytes(b"x")
    newest = snap_dir / "2026-04-12.db"
    newest.write_bytes(b"y")

    health = compute_memory_health(trw)
    # Newest file is fresh → age 0.
    assert health["last_snapshot_age_hours"] == 0


def test_snapshot_age_none_when_dir_missing(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    health = compute_memory_health(trw)
    assert health["last_snapshot_age_hours"] is None


def test_snapshot_age_none_when_dir_empty(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    (trw / "memory" / "snapshots" / "daily").mkdir(parents=True)
    health = compute_memory_health(trw)
    assert health["last_snapshot_age_hours"] is None


# ---------------------------------------------------------------------------
# Concurrent writers
# ---------------------------------------------------------------------------


def test_concurrent_writers_counts_live_pids(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    db = trw / "memory" / "memory.db"
    writers_dir = db.parent / f"{db.name}.writers"
    writers_dir.mkdir()
    # Our own pid is definitely live.
    (writers_dir / f"{os.getpid()}.lock").write_text("")
    health = compute_memory_health(trw)
    assert health["concurrent_writers"] >= 1


def test_concurrent_writers_zero_when_no_registry(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    health = compute_memory_health(trw)
    assert health["concurrent_writers"] == 0


def test_concurrent_writers_ignores_dead_pids(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    db = trw / "memory" / "memory.db"
    writers_dir = db.parent / f"{db.name}.writers"
    writers_dir.mkdir()
    (writers_dir / "9999998.lock").write_text("")  # likely dead
    health = compute_memory_health(trw)
    # On Linux, dead pid is filtered out.
    # 0 if only the dead one was present.
    assert health["concurrent_writers"] == 0


# ---------------------------------------------------------------------------
# Integrity scheduler sentinel
# ---------------------------------------------------------------------------


def test_integrity_scheduler_age_from_sentinel(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    sentinel = trw / "memory" / ".integrity_last_check"
    # 30 minutes ago.
    sentinel.write_text(str(time.time() - 30 * 60))
    health = compute_memory_health(trw)
    age = health["last_integrity_check_age_minutes"]
    assert age is not None
    assert 29 <= age <= 31


def test_integrity_scheduler_age_none_when_sentinel_missing(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    health = compute_memory_health(trw)
    assert health["last_integrity_check_age_minutes"] is None


def test_integrity_scheduler_age_none_when_sentinel_garbage(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    sentinel = trw / "memory" / ".integrity_last_check"
    sentinel.write_text("not a float")
    health = compute_memory_health(trw)
    assert health["last_integrity_check_age_minutes"] is None


# ---------------------------------------------------------------------------
# Integrity probe
# ---------------------------------------------------------------------------


def test_integrity_ok_on_healthy_db(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    health = compute_memory_health(trw)
    assert health["integrity_ok"] is True


def test_integrity_fails_on_corrupt_db(tmp_path: Path) -> None:
    trw = _make_trw_dir(tmp_path)
    db = trw / "memory" / "memory.db"
    data = bytearray(db.read_bytes())
    for offset in range(4096, 4196):
        if offset < len(data):
            data[offset] = 0xFF
    db.write_bytes(bytes(data))
    health = compute_memory_health(trw)
    assert health["integrity_ok"] is False


# ---------------------------------------------------------------------------
# Failure resilience
# ---------------------------------------------------------------------------


def test_nonexistent_trw_dir_returns_empty_or_safe_defaults(tmp_path: Path) -> None:
    """Dashboard MUST NOT raise even if trw_dir is bogus."""
    health = compute_memory_health(tmp_path / "does_not_exist")
    # Either empty dict or safe defaults — both acceptable.
    assert isinstance(health, dict)
    # If populated, defaults are benign.
    if health.get("integrity_ok") is not None:
        assert health["integrity_ok"] is True  # missing DB counts as "ok"
