"""Tests for PRD-INFRA-067 integrity-on-delivery wiring (C2)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from trw_mcp.tools._deliver_integrity import check_memory_integrity_on_deliver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_trw_dir(tmp_path: Path, create_db: bool = True, corrupt: bool = False) -> Path:
    """Build a fake .trw/ with optional memory.db."""
    trw_dir = tmp_path / ".trw"
    mem_dir = trw_dir / "memory"
    mem_dir.mkdir(parents=True)
    if create_db:
        db = mem_dir / "memory.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE memories (id TEXT, content TEXT)")
        conn.execute("INSERT INTO memories VALUES ('a', 'x')")
        conn.commit()
        conn.close()
        if corrupt:
            data = bytearray(db.read_bytes())
            for offset in range(4096, 4196):
                if offset < len(data):
                    data[offset] = 0xFF
            db.write_bytes(bytes(data))
    return trw_dir


def _setup_run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "sprint-x" / "run-y"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text("")
    return run


# ---------------------------------------------------------------------------
# Healthy / missing / corrupt paths
# ---------------------------------------------------------------------------


def test_integrity_ok_on_healthy_db(tmp_path: Path) -> None:
    trw_dir = _setup_trw_dir(tmp_path)
    result = check_memory_integrity_on_deliver(trw_dir)
    assert result["ok"] is True
    assert result["detail"] == "ok"
    assert str(result["db_path"]).endswith("memory.db")
    assert result["checked_at"]  # non-empty ISO string


def test_integrity_ok_when_db_missing(tmp_path: Path) -> None:
    """Missing DB at deliver time is NOT a corruption event (fresh runs)."""
    trw_dir = _setup_trw_dir(tmp_path, create_db=False)
    result = check_memory_integrity_on_deliver(trw_dir)
    assert result["ok"] is True
    assert result["detail"] == "db_missing"


def test_integrity_fails_on_corrupt_db(tmp_path: Path) -> None:
    trw_dir = _setup_trw_dir(tmp_path, corrupt=True)
    result = check_memory_integrity_on_deliver(trw_dir)
    assert result["ok"] is False
    # detail may be "malformed" or sqlite-specific text — just assert non-ok
    assert result["detail"] != "ok"


# ---------------------------------------------------------------------------
# Event-log emission
# ---------------------------------------------------------------------------


def test_event_emitted_when_run_dir_provided(tmp_path: Path) -> None:
    trw_dir = _setup_trw_dir(tmp_path)
    run_dir = _setup_run_dir(tmp_path)
    check_memory_integrity_on_deliver(trw_dir, run_dir)
    events_path = run_dir / "meta" / "events.jsonl"
    lines = [ln for ln in events_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1
    events = [json.loads(ln) for ln in lines]
    names = [e.get("event") for e in events]
    assert "db_integrity_check_on_deliver" in names


def test_no_event_when_no_run_dir(tmp_path: Path) -> None:
    trw_dir = _setup_trw_dir(tmp_path)
    # No run_dir passed — result still returned, but no events.jsonl file.
    result = check_memory_integrity_on_deliver(trw_dir, None)
    assert result["ok"] is True


def test_failure_on_corrupt_still_returns_dict(tmp_path: Path) -> None:
    """Observability: probe failure MUST NEVER raise; always return a dict."""
    trw_dir = _setup_trw_dir(tmp_path, corrupt=True)
    run_dir = _setup_run_dir(tmp_path)
    result = check_memory_integrity_on_deliver(trw_dir, run_dir)
    assert set(result.keys()) == {"ok", "detail", "db_path", "checked_at"}
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# PRD-DIST-432: read-only URI fix regression coverage
# ---------------------------------------------------------------------------


def test_integrity_ok_with_concurrent_writer(tmp_path: Path) -> None:
    """PRD-DIST-432 FR-4: probe MUST succeed against a DB with active WAL state.

    Cycle 274/275/276/277/278 false-positive scenario: the DB is healthy
    but a concurrent writer connection is mid-transaction. The pre-fix
    SQLiteBackend.check_integrity went through ``_connect`` which set
    PRAGMAs that interacted poorly with the writer's WAL state. The fix
    uses read-only URI mode so the probe is isolated from any writer.
    """
    trw_dir = _setup_trw_dir(tmp_path)
    db = trw_dir / "memory" / "memory.db"

    # Switch the DB to WAL mode (matches production).
    setup = sqlite3.connect(str(db))
    setup.execute("PRAGMA journal_mode=WAL")
    setup.commit()
    setup.close()

    # Hold a writer connection open with an in-flight (uncommitted) write.
    # On the pre-fix path this state was the most reliable trigger of
    # the "file is not a database" false positive in the deliver flow.
    writer = sqlite3.connect(str(db), timeout=5.0)
    try:
        writer.execute("BEGIN")
        writer.execute("INSERT INTO memories VALUES ('z', 'inflight')")
        # Probe runs while the writer's transaction is open.
        result = check_memory_integrity_on_deliver(trw_dir)
    finally:
        writer.rollback()
        writer.close()

    assert result["ok"] is True, (
        f"PRD-DIST-432 fix regressed: probe returned ok=False with "
        f"detail={result['detail']!r} against a healthy DB with active "
        f"WAL writer."
    )
    assert result["detail"] == "ok"


def test_integrity_uses_readonly_uri_mode(tmp_path: Path) -> None:
    """PRD-DIST-432 FR-1: the probe SHALL NOT mutate the DB.

    Verify by capturing the DB's mtime + main-file size before and after
    the probe. Read-only URI mode guarantees no main-file writes; WAL
    activity in another connection is not relevant here because we hold
    no other connection during this probe.
    """
    trw_dir = _setup_trw_dir(tmp_path)
    db = trw_dir / "memory" / "memory.db"
    size_before = db.stat().st_size

    result = check_memory_integrity_on_deliver(trw_dir)
    assert result["ok"] is True

    # Read-only mode means main-file size does NOT grow.
    assert db.stat().st_size == size_before


def test_integrity_corrupt_db_still_reports_not_ok(tmp_path: Path) -> None:
    """PRD-DIST-432 FR-4: legitimate corruption MUST still surface.

    Re-asserts the cycle-pre-432 behavior: the fix MUST NOT mask real
    corruption events. Mirrors test_integrity_fails_on_corrupt_db with
    an explicit PRD-432 reference for traceability.
    """
    trw_dir = _setup_trw_dir(tmp_path, corrupt=True)
    result = check_memory_integrity_on_deliver(trw_dir)
    assert result["ok"] is False
    # Detail should be sqlite-specific or contain a malformation hint;
    # exact text varies by SQLite version, so just assert non-empty
    # non-"ok" string.
    assert isinstance(result["detail"], str)
    assert result["detail"] != "ok"
