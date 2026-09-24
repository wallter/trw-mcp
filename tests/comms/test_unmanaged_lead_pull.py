"""CORE-296 FR04: an unmanaged lead sees body-free pending facts by pulling discover."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env, write_pin  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms
from trw_mcp import formation
from trw_mcp.comms._store import database_path


def test_unmanaged_lead_discovers_pending_count_and_earliest_age_without_fetch(
    formation_env: FormationFixture, comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_comms(monkeypatch)
    fixture = formation_env
    formation.create(
        fixture.orchestrator_run,
        {
            **fixture.payload(),
            "members": [
                {"member_id": "lead", "client": "claude-code", "open_join": True},
                {"member_id": "impl-2", "client": "codex", "open_join": True},
            ],
        },
        trw_dir=fixture.trw_dir,
    )
    formation.join("release-train", "lead", fixture.orchestrator_run, pin_key="pin-orch", trw_dir=fixture.trw_dir)
    formation.join("release-train", "impl-2", fixture.member_runs["impl-2"], pin_key="pin-b", trw_dir=fixture.trw_dir)
    write_pin(fixture, "pin-orch", fixture.orchestrator_run)
    write_pin(fixture, "pin-b", fixture.member_runs["impl-2"])
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    assert call_peers(comms_server, "discover")["lead_pending"] == {
        "measurement": "measured",
        "count": 0,
        "earliest_age_seconds": None,
    }
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    assert "lead_pending" not in call_peers(comms_server, "discover")
    sent = asyncio.run(
        comms_server.call_tool(
            "trw_send", {"recipient_member_id": "lead", "request_key": "old", "body": "PRIVATE BODY"}
        )
    )
    assert sent.structured_content["status"] == "ok"
    database = database_path(fixture.manifest_path())
    with sqlite3.connect(database) as conn:
        admitted = float(conn.execute("SELECT admitted_at FROM admissions").fetchone()[0])
        before = conn.execute("SELECT state FROM admissions").fetchall()
    monkeypatch.setattr("trw_mcp.comms._bootstrap.time.time", lambda: admitted + 601)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    discovered = call_peers(comms_server, "discover")
    assert discovered["lead_pending"] == {
        "measurement": "measured",
        "count": 1,
        "earliest_age_seconds": 601,
    }
    assert "PRIVATE BODY" not in repr(discovered)
    assert "pin-b" not in repr(discovered)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT state FROM admissions").fetchall() == before
        conn.execute("UPDATE admissions SET expires_at=?", (admitted + 600,))
    assert call_peers(comms_server, "discover")["lead_pending"] == {
        "measurement": "measured",
        "count": 0,
        "earliest_age_seconds": None,
    }
    monkeypatch.setattr("trw_mcp.comms._store.database_path", lambda _path: fixture.project_root / "missing.sqlite")
    assert call_peers(comms_server, "discover")["lead_pending"] == {"measurement": "not_measured"}
