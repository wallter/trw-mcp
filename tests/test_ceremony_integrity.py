"""Tests for PRD-INFRA-067 integrity-on-delivery wiring (C2)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

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
