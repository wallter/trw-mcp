"""Unit tests for :mod:`trw_mcp.tools.mcp_security_status` (FR-5 / FR-7).

PRD-CORE-300 slice S3a moved the MCP tool this used to register to
``trw-mcp telemetry security`` (``tools/_telemetry_cli.py``).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.tools.mcp_security_status import (
    MCPSecurityStatus,
    compute_security_status,
)

pytestmark = pytest.mark.integration


def _write_event(events_dir: Path, *, decision: str, ts: datetime) -> None:
    events_dir.mkdir(parents=True, exist_ok=True)
    fname = f"events-{ts.strftime('%Y-%m-%d')}.jsonl"
    row = {
        "event_id": f"evt_{decision}_{int(ts.timestamp())}",
        "session_id": "s",
        "ts": ts.isoformat(),
        "emitter": "mcp_security",
        "event_type": "mcp_security",
        "payload": {"decision": decision, "transport": "stdio"},
    }
    with (events_dir / fname).open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def test_status_defaults_to_prd_shape(tmp_path: Path) -> None:
    status = compute_security_status(events_dir=tmp_path / "no_events")
    assert isinstance(status, MCPSecurityStatus)
    assert status.registered_servers == []
    assert status.allowlist_hash == ""
    assert status.recent_anomalies == []
    assert status.quarantined_servers == []


def test_status_includes_registered_servers_and_allowlist_hash(tmp_path: Path) -> None:
    status = compute_security_status(
        events_dir=tmp_path / "no_events",
        registered_servers=["trw", "filesystem"],
        allowlist_hash="abc123",
    )
    assert status.registered_servers == ["trw", "filesystem"]
    assert status.allowlist_hash == "abc123"


def test_status_includes_recent_anomalies_and_quarantine(tmp_path: Path) -> None:
    events_dir = tmp_path / "ctx"
    now = datetime.now(tz=timezone.utc)
    _write_event(events_dir, decision="shadow_anomaly", ts=now)
    _write_event(events_dir, decision="shadow_anomaly", ts=now - timedelta(hours=1))
    _write_event(events_dir, decision="shadow_deny", ts=now)
    _write_event(events_dir, decision="shadow_anomaly", ts=now - timedelta(hours=48))

    status = compute_security_status(
        events_dir=events_dir,
        quarantined_servers=["filesystem"],
        now=now,
    )
    assert len(status.recent_anomalies) == 2
    assert status.quarantined_servers == ["filesystem"]


def test_status_reads_legacy_tool_call_projection_for_recent_anomalies(tmp_path: Path) -> None:
    events_dir = tmp_path / "ctx"
    events_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    row = {
        "event_id": "evt_projection",
        "session_id": "s",
        "ts": now.isoformat(),
        "emitter": "mcp_security",
        "event_type": "mcp_security",
        "payload": {
            "decision": "shadow_anomaly",
            "transport": "stdio",
            "server": "filesystem",
            "tool": "read_file",
            "anomaly_type": "novel_arg_pattern",
        },
    }
    with (events_dir / "tool_call_events.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")

    status = compute_security_status(events_dir=events_dir, now=now)

    assert status.recent_anomalies == [
        {
            "ts": now.isoformat(),
            "server": "filesystem",
            "tool": "read_file",
            "type": "novel_arg_pattern",
        }
    ]


def test_status_cli_command_produces_the_correct_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-7 (CLI form, PRD-CORE-300 slice S3a): ``trw-mcp telemetry security --json``."""
    from trw_mcp.server._cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["trw-mcp", "telemetry", "security", "--json"])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code) if isinstance(exc.code, int) else 1
    out = capsys.readouterr().out
    assert code == 0, out
    result = json.loads(out)
    for key in (
        "registered_servers",
        "allowlist_hash",
        "recent_anomalies",
        "quarantined_servers",
    ):
        assert key in result
    validated = MCPSecurityStatus(**result)
    assert validated.quarantined_servers == []
