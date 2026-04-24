"""Unit tests for :mod:`trw_mcp.middleware.mcp_security` (FR-2 / FR-6 / FR-9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.middleware.mcp_security import (
    CLAUDE_CODE_PREFIX,
    TRANSPORTS,
    AdvertisedTool,
    MCPSecurityMiddleware,
    normalize_tool_name,
)
from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig
from trw_mcp.security.mcp_registry import MCPAllowlist, MCPServer

pytestmark = pytest.mark.integration


def _make_middleware(tmp_path: Path) -> MCPSecurityMiddleware:
    allowlist = MCPAllowlist(
        servers=[
            MCPServer(
                name="trw",
                url_or_command="trw-mcp",
                signer="trw-maintainer",
                signature="stub:v1",
                trust_level="verified",
                capabilities=["trw_recall", "trw_learn"],
            ),
        ]
    )
    cfg = AnomalyDetectorConfig(shadow_clock_path=tmp_path / "sec" / "clock.yaml")
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    return MCPSecurityMiddleware(
        allowlist=allowlist,
        scopes={},
        anomaly_detector=det,
        run_dir=None,
        fallback_dir=tmp_path,
    )


def test_normalize_tool_name_strips_claude_code_prefix() -> None:
    assert normalize_tool_name(f"{CLAUDE_CODE_PREFIX}trw_recall") == "trw_recall"
    assert normalize_tool_name("trw_recall") == "trw_recall"
    assert normalize_tool_name("other") == "other"


def test_filter_advertised_tools_rejects_unlisted_server(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    ads = [AdvertisedTool(server="evil", name="exec_shell")]
    out = mw.filter_advertised_tools(transport="stdio", advertisements=ads)
    assert out == []


def test_filter_advertised_tools_rejects_tool_not_in_capabilities(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    ads = [AdvertisedTool(server="trw", name="exec_shell")]
    out = mw.filter_advertised_tools(transport="stdio", advertisements=ads)
    assert out == []


def test_filter_advertised_tools_normalizes_claude_code_prefix(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    ads = [AdvertisedTool(server="trw", name=f"{CLAUDE_CODE_PREFIX}trw_recall")]
    out = mw.filter_advertised_tools(transport="stdio", advertisements=ads)
    assert len(out) == 1
    assert out[0].name == "trw_recall"
    assert out[0].namespaced_name == f"{CLAUDE_CODE_PREFIX}trw_recall"


def test_on_tool_call_fires_all_three_layers(tmp_path: Path) -> None:
    """FR-9: every tool-call dispatch fires registry, scope, anomaly."""
    mw = _make_middleware(tmp_path)
    decision = mw.on_tool_call(
        transport="stdio",
        server="trw",
        tool="trw_recall",
        args={"q": "x"},
    )
    assert decision.layers_fired == ["registry", "capability_scope", "anomaly_detector"]
    assert decision.allowed is True  # observe-mode


def test_on_tool_call_allowed_even_on_deny_reason(tmp_path: Path) -> None:
    """Observe-mode invariant: allowed=True even when reason is set."""
    mw = _make_middleware(tmp_path)
    decision = mw.on_tool_call(
        transport="http",
        server="evil",
        tool="exec_shell",
    )
    assert decision.allowed is True
    assert decision.reason == "server_not_in_allowlist"


def test_all_three_transports_emit_events(tmp_path: Path) -> None:
    """FR-9 reachability: stdio, http, sse all write MCPSecurityEvent rows."""
    mw = _make_middleware(tmp_path)
    for transport in TRANSPORTS:
        mw.on_tool_call(
            transport=transport,
            server="trw",
            tool="trw_recall",
            args={},
        )
    events_files = list(tmp_path.glob("events-*.jsonl"))
    assert len(events_files) == 1
    rows = [json.loads(line) for line in events_files[0].read_text().splitlines() if line]
    transports_seen = {
        row["payload"].get("transport")
        for row in rows
        if row.get("event_type") == "mcp_security"
        and isinstance(row.get("payload"), dict)
        and "transport" in row["payload"]
    }
    assert transports_seen == set(TRANSPORTS)


def test_middleware_never_raises_on_unknown_server(tmp_path: Path) -> None:
    """Observe-mode invariant: never raises regardless of input."""
    mw = _make_middleware(tmp_path)
    mw.on_tool_call(transport="sse", server="ghost", tool="haunt", args={"evil": True})
