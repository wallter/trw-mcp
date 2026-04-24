"""Unit tests for :mod:`trw_mcp.middleware.mcp_security` (FR-2 / FR-6 / FR-9)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.tools import Tool
from mcp.types import TextContent

from trw_mcp.middleware.mcp_security import (
    CLAUDE_CODE_PREFIX,
    TRANSPORTS,
    AdvertisedTool,
    MCPSecurityMiddleware,
    normalize_transport,
    normalize_tool_name,
)
from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig
from trw_mcp.security.capability_scope import CapabilityScope
from trw_mcp.security.mcp_registry import AllowedTool, MCPAllowlist, MCPServer
from trw_mcp.state._paths import pin_active_run, unpin_active_run

pytestmark = pytest.mark.integration


def _make_middleware(tmp_path: Path) -> MCPSecurityMiddleware:
    allowlist = MCPAllowlist(
        servers=[
            MCPServer(
                name="trw",
                url_or_command="trw-mcp",
                public_key_fingerprint="sha256:trw",
                allowed_tools=[
                    AllowedTool(
                        name="trw_recall",
                        allowed_phases=("implement",),
                        allowed_scopes=("read",),
                    ),
                    AllowedTool(
                        name="trw_learn",
                        allowed_phases=("implement",),
                        allowed_scopes=("write",),
                    ),
                ],
            ),
            MCPServer(
                name="filesystem",
                url_or_command="npx -y @modelcontextprotocol/server-filesystem",
                public_key_fingerprint="sha256:filesystem",
                allowed_tools=[
                    AllowedTool(
                        name="read_file",
                        allowed_phases=("implement",),
                        allowed_scopes=("read",),
                    ),
                ],
            ),
        ]
    )
    cfg = AnomalyDetectorConfig(shadow_clock_path=tmp_path / "sec" / "clock.yaml")
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    return MCPSecurityMiddleware(
        allowlist=allowlist,
        scopes={
            "trw_recall": CapabilityScope(
                server_name="trw",
                tool_name="trw_recall",
                allowed_phases=("implement",),
                allowed_scopes=("read",),
            ),
            "read_file": CapabilityScope(
                server_name="filesystem",
                tool_name="read_file",
                allowed_phases=("implement",),
                allowed_scopes=("read",),
            ),
        },
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


def test_filter_advertised_tools_uses_namespaced_peer_server_identity(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    ads = [AdvertisedTool(server="trw", name="filesystem__read_file")]
    out = mw.filter_advertised_tools(transport="stdio", advertisements=ads)
    assert len(out) == 1
    assert out[0].server == "filesystem"
    assert out[0].name == "read_file"


def test_filter_advertised_tools_uses_runtime_fingerprint_when_available(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    ads = [AdvertisedTool(server="filesystem", name="read_file")]

    allowed = mw.filter_advertised_tools(
        transport="sse",
        advertisements=ads,
        fastmcp_context=SimpleNamespace(
            request_context=SimpleNamespace(
                request=SimpleNamespace(headers={"x-trw-mcp-fingerprint": "sha256:filesystem"})
            )
        ),
    )
    blocked = mw.filter_advertised_tools(
        transport="sse",
        advertisements=ads,
        fastmcp_context=SimpleNamespace(
            request_context=SimpleNamespace(
                request=SimpleNamespace(headers={"x-trw-mcp-fingerprint": "sha256:drifted-filesystem"})
            )
        ),
    )

    assert [tool.name for tool in allowed] == ["read_file"]
    assert blocked == []


def test_filter_advertised_tools_emits_runtime_constraint_when_fingerprint_missing(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)

    allowed = mw.filter_advertised_tools(
        transport="stdio",
        advertisements=[AdvertisedTool(server="trw", name="trw_recall")],
        session_id="sess-advertise",
    )

    assert [tool.name for tool in allowed] == ["trw_recall"]
    events_file = next(tmp_path.glob("events-*.jsonl"))
    payloads = [
        json.loads(line)["payload"]
        for line in events_file.read_text().splitlines()
        if line
    ]
    assert any(payload.get("fingerprint_constraint") == "runtime_did_not_expose_peer_fingerprint" for payload in payloads)
    assert any(payload.get("identity_verification") == "server_name_only_runtime_constraint" for payload in payloads)


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


def test_on_tool_call_blocks_unlisted_server(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    decision = mw.on_tool_call(
        transport="streamable-http",
        server="evil",
        tool="exec_shell",
    )
    assert decision.allowed is False
    assert decision.reason == "server_not_in_allowlist"


def test_all_three_transports_emit_events(tmp_path: Path) -> None:
    """FR-9 reachability: stdio, streamable-http, sse all write events."""
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


def test_middleware_blocks_quarantined_server(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    mw._registry.quarantine("trw", reason="signature_drift")
    decision = mw.on_tool_call(transport="sse", server="trw", tool="trw_recall", args={})
    assert decision.allowed is False
    assert decision.reason == "signature_drift"


@pytest.mark.asyncio
async def test_on_call_tool_uses_live_namespaced_peer_and_runtime_fingerprint(
    tmp_path: Path,
) -> None:
    mw = _make_middleware(tmp_path)

    class _Ctx:
        def __init__(self, fingerprint: str) -> None:
            self.message = SimpleNamespace(name="filesystem__read_file", arguments={"path": "README.md"})
            self.fastmcp_context = SimpleNamespace(
                transport="sse",
                session_id="sess-1",
                request_context=SimpleNamespace(
                    request=SimpleNamespace(headers={"x-trw-mcp-fingerprint": fingerprint})
                ),
            )

    async def call_next(_: object) -> object:
        from fastmcp.tools import ToolResult

        return ToolResult(content=[TextContent(type="text", text="ok")], structured_content={})

    allowed = await mw.on_call_tool(_Ctx("sha256:filesystem"), call_next)
    blocked = await mw.on_call_tool(_Ctx("sha256:drifted-filesystem"), call_next)

    assert allowed.structured_content == {}
    assert blocked.structured_content == {
        "error": "mcp_security_blocked",
        "server": "filesystem",
        "tool": "read_file",
        "reason": "signature_drift",
    }


def test_unsigned_admission_emits_operator_audit_fields(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    mw._registry.allow_unsigned = True
    decision = mw.on_tool_call(
        transport="stdio",
        server="ghost",
        tool="exec_shell",
        args={"cmd": "echo hi"},
        session_id="sess-audit",
    )
    assert decision.allowed is True
    events_files = list(tmp_path.glob("events-*.jsonl"))
    rows = [json.loads(line) for line in events_files[0].read_text().splitlines() if line]
    payloads = [row["payload"] for row in rows if row.get("event_type") == "mcp_security"]
    assert any(payload.get("unsigned_admission") is True for payload in payloads)
    assert any(bool(payload.get("operator")) for payload in payloads)


def test_anomaly_enforce_mode_blocks_rate_spike(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    mw._detector._config = mw._detector._config.model_copy(update={"mode": "enforce", "sigma_threshold": 3.0})
    mw._detector.seed_baseline(
        known_pairs={("trw", "trw_recall")},
        historical_rates={("trw", "trw_recall"): [1.0, 1.0, 2.0, 1.0, 1.0, 2.0]},
    )

    decision = None
    for _ in range(30):
        decision = mw.on_tool_call(
            transport="stdio",
            server="trw",
            tool="trw_recall",
            args={"q": "burst"},
            session_id="sess-rate",
        )

    assert decision is not None
    assert decision.allowed is False
    assert decision.reason == "rate_spike"


def test_quarantine_auto_release_honors_matching_fingerprint(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    mw._quarantine_auto_release = True

    blocked = mw.on_tool_call(
        transport="sse",
        server="filesystem",
        tool="read_file",
        args={"path": "README.md"},
        observed_fingerprint="sha256:drifted-filesystem",
    )
    released = mw.on_tool_call(
        transport="sse",
        server="filesystem",
        tool="read_file",
        args={"path": "README.md"},
        observed_fingerprint="sha256:filesystem",
    )

    assert blocked.allowed is False
    assert blocked.reason == "signature_drift"
    assert released.allowed is True
    assert "filesystem" not in mw._registry.quarantined_servers


def test_streamable_http_transport_is_canonical() -> None:
    assert normalize_transport("http") == "streamable-http"
    assert normalize_transport("streamable-http") == "streamable-http"


def test_build_middleware_mounts_mcp_security() -> None:
    from trw_mcp.models.config import TRWConfig, reload_config

    reload_config(TRWConfig(meta_tune={"enabled": False}))
    from trw_mcp.server._app import _build_middleware

    middleware = _build_middleware()

    assert any(isinstance(item, MCPSecurityMiddleware) for item in middleware)


@pytest.mark.asyncio
async def test_on_list_tools_filters_real_tool_advertisements(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)

    async def call_next(_: object) -> list[Tool]:
        return [
            Tool(name="trw_recall", parameters={}),
            Tool(name="exec_shell", parameters={}),
        ]

    class _Ctx:
        def __init__(self) -> None:
            self.message = object()
            self.fastmcp_context = None

    filtered = await mw.on_list_tools(_Ctx(), call_next)
    assert [tool.name for tool in filtered] == ["trw_recall"]


@pytest.mark.asyncio
async def test_on_call_tool_writes_mounted_event_with_pinned_run_id(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    run_dir = tmp_path / "runs" / "task-a" / "run-mounted"
    (run_dir / "meta").mkdir(parents=True)
    pin_active_run(run_dir, session_id="sess-mounted")

    class _Ctx:
        def __init__(self) -> None:
            self.message = SimpleNamespace(name="trw_recall", arguments={"q": "README"})
            self.fastmcp_context = SimpleNamespace(transport="stdio", session_id="sess-mounted")

    async def call_next(_: object) -> object:
        from fastmcp.tools import ToolResult

        return ToolResult(content=[TextContent(type="text", text="ok")], structured_content={})

    try:
        result = await mw.on_call_tool(_Ctx(), call_next)
    finally:
        unpin_active_run(session_id="sess-mounted")

    assert result.structured_content == {}
    events_file = next((run_dir / "meta").glob("events-*.jsonl"))
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    security_rows = [row for row in rows if row.get("event_type") == "mcp_security"]
    assert security_rows
    assert all(row["run_id"] == "run-mounted" for row in security_rows)


@pytest.mark.asyncio
async def test_on_list_tools_writes_mounted_event_with_pinned_run_id(tmp_path: Path) -> None:
    mw = _make_middleware(tmp_path)
    run_dir = tmp_path / "runs" / "task-a" / "run-advertise"
    (run_dir / "meta").mkdir(parents=True)
    pin_active_run(run_dir, session_id="sess-advertise-mounted")

    async def call_next(_: object) -> list[Tool]:
        return [Tool(name="trw_recall", parameters={})]

    class _Ctx:
        def __init__(self) -> None:
            self.message = object()
            self.fastmcp_context = SimpleNamespace(transport="stdio", session_id="sess-advertise-mounted")

    try:
        filtered = await mw.on_list_tools(_Ctx(), call_next)
    finally:
        unpin_active_run(session_id="sess-advertise-mounted")

    assert [tool.name for tool in filtered] == ["trw_recall"]
    events_file = next((run_dir / "meta").glob("events-*.jsonl"))
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    security_rows = [row for row in rows if row.get("event_type") == "mcp_security"]
    assert security_rows
    assert all(row["run_id"] == "run-advertise" for row in security_rows)
