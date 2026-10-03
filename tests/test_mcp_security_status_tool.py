"""Unit tests for :mod:`trw_mcp.tools.mcp_security_status` (FR-5 / FR-7).

PRD-CORE-300 slice S3a moved the MCP tool this used to register to
``trw-mcp telemetry security`` (``tools/_telemetry_cli.py``).
"""

from __future__ import annotations

import json
import os
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


def test_status_reads_run_scoped_unified_events_from_project_context(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    context_dir = trw_dir / "context"
    run_events = trw_dir / "runs" / "task" / "run-1" / "meta" / "events-2026-09-26.jsonl"
    context_dir.mkdir(parents=True)
    run_events.parent.mkdir(parents=True)
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    row = {
        "event_id": "evt_run_projection",
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
    run_events.write_text(json.dumps(row) + "\n", encoding="utf-8")

    status = compute_security_status(events_dir=context_dir, now=now)

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


def test_status_skips_run_event_files_untouched_inside_the_window(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    context_dir = trw_dir / "context"
    run_events = trw_dir / "runs" / "task" / "run-old" / "meta" / "events-2026-09-20.jsonl"
    context_dir.mkdir(parents=True)
    run_events.parent.mkdir(parents=True)
    now = datetime.now(tz=timezone.utc)
    row = {
        "event_id": "evt_old_run",
        "ts": now.isoformat(),
        "event_type": "mcp_security",
        "payload": {"decision": "shadow_anomaly", "server": "s", "tool": "t", "anomaly_type": "x"},
    }
    run_events.write_text(json.dumps(row) + "\n", encoding="utf-8")
    stale = (now - timedelta(hours=72)).timestamp()
    os.utime(run_events, (stale, stale))

    assert compute_security_status(events_dir=context_dir, now=now).recent_anomalies == []
