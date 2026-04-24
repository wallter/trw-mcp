"""Unit tests for :mod:`trw_mcp.tools.mcp_security_status` (FR-5 / FR-7)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from trw_mcp.tools.mcp_security_status import (
    MCPSecurityStatus,
    compute_security_status,
)

pytestmark = pytest.mark.integration


def _write_event(events_dir: Path, *, decision: str, ts: datetime) -> None:
    events_dir.mkdir(parents=True, exist_ok=True)
    fname = f"events-{ts.strftime('%Y-%m-%d')}.jsonl"
    row = {
        "event_id": "evt_test",
        "session_id": "s",
        "ts": ts.isoformat(),
        "emitter": "mcp_security",
        "event_type": "mcp_security",
        "payload": {"decision": decision, "transport": "stdio"},
    }
    with (events_dir / fname).open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def test_status_defaults_observe_mode(tmp_path: Path) -> None:
    status = compute_security_status(
        shadow_clock_path=tmp_path / "missing.yaml",
        events_dir=tmp_path / "no_events",
        enforce_mode=False,
    )
    assert isinstance(status, MCPSecurityStatus)
    assert status.registry_mode == "observe"
    assert status.enforce_mode is False
    assert status.shadow_clock_start == ""
    assert status.anomaly_count_last_24h == 0
    assert status.capability_scope_denials_last_24h == 0


def test_status_reads_shadow_clock(tmp_path: Path) -> None:
    clock = tmp_path / "clock.yaml"
    clock.write_text(
        yaml.safe_dump(
            {
                "started_at": "2026-04-23T00:00:00+00:00",
                "phase": "shadow",
                "threshold_review_at": "2026-05-14T00:00:00+00:00",
            }
        )
    )
    status = compute_security_status(
        shadow_clock_path=clock,
        events_dir=tmp_path / "no_events",
    )
    assert status.shadow_clock_start == "2026-04-23T00:00:00+00:00"


def test_status_counts_anomaly_and_deny_events(tmp_path: Path) -> None:
    events_dir = tmp_path / "ctx"
    now = datetime.now(tz=timezone.utc)
    _write_event(events_dir, decision="shadow_anomaly", ts=now)
    _write_event(events_dir, decision="shadow_anomaly", ts=now - timedelta(hours=1))
    _write_event(events_dir, decision="shadow_deny", ts=now)
    _write_event(events_dir, decision="shadow_allow", ts=now)
    # Old event (> 24h) should not be counted
    _write_event(events_dir, decision="shadow_anomaly", ts=now - timedelta(hours=48))

    status = compute_security_status(
        shadow_clock_path=tmp_path / "none.yaml",
        events_dir=events_dir,
        now=now,
    )
    assert status.anomaly_count_last_24h == 2
    assert status.capability_scope_denials_last_24h == 1


def test_status_tool_registered_in_server() -> None:
    """FR-7: tool is registered and produces the correct shape."""
    from tests.conftest import extract_tool_fn, make_test_server

    # Register on a fresh FastMCP instance
    from fastmcp import FastMCP

    from trw_mcp.tools.mcp_security_status import register_mcp_security_status

    srv = FastMCP("test")
    register_mcp_security_status(srv)
    fn = extract_tool_fn(srv, "trw_mcp_security_status")
    result = fn()
    for key in (
        "registry_mode",
        "shadow_clock_start",
        "anomaly_count_last_24h",
        "capability_scope_denials_last_24h",
        "enforce_mode",
    ):
        assert key in result
    # Ensure shape matches Pydantic model
    validated = MCPSecurityStatus(**result)
    assert validated.enforce_mode is False
    _ = make_test_server  # imported for conftest side-effect parity
