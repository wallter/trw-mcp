"""Regression tests for PRD-FIX-089 stdio proxy handshake budgets."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _transport
from trw_mcp.server._proxy import discover_proxy_capabilities
from trw_mcp.server._rate_limit import LocalTokenBucketMiddleware, TokenBucket


@dataclass
class _Result:
    tools: list[str] | None = None
    resources: list[str] | None = None
    prompts: list[str] | None = None


class _FastSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def initialize(self) -> None:
        self.calls.append("initialize")

    async def list_tools(self) -> _Result:
        self.calls.append("list_tools")
        return _Result(tools=["trw_session_start", "trw_build_check"])

    async def list_resources(self) -> _Result:
        self.calls.append("list_resources")
        return _Result(resources=["trw://run/state"])

    async def list_prompts(self) -> _Result:
        self.calls.append("list_prompts")
        return _Result(prompts=["prd_create"])


class _SlowSession(_FastSession):
    async def list_tools(self) -> _Result:
        self.calls.append("list_tools")
        await asyncio.sleep(1.0)
        return _Result(tools=[])


@pytest.mark.unit
async def test_discover_proxy_capabilities_success() -> None:
    session = _FastSession()

    capabilities = await discover_proxy_capabilities(session, timeout_seconds=1.0)

    assert session.calls == ["initialize", "list_tools", "list_resources", "list_prompts"]
    assert capabilities.tools_result.tools == ["trw_session_start", "trw_build_check"]
    assert capabilities.resources_result.resources == ["trw://run/state"]
    assert capabilities.prompts_result.prompts == ["prd_create"]


@pytest.mark.unit
async def test_discover_proxy_capabilities_times_out_within_budget() -> None:
    session = _SlowSession()

    with pytest.raises(TimeoutError, match="proxy capability discovery timed out"):
        await discover_proxy_capabilities(session, timeout_seconds=0.01)

    assert session.calls == ["initialize", "list_tools"]


@pytest.mark.unit
def test_proxy_handshake_timeout_default_stays_inside_client_reconnect_window() -> None:
    config = TRWConfig()

    assert config.mcp_proxy_handshake_timeout_seconds == 8.0
    # Three attempts with 1s and 2s backoff should stay below the common 30s
    # Claude/Cursor reconnect timeout when each attempt uses the default budget.
    assert (3 * config.mcp_proxy_handshake_timeout_seconds) + 3 < 30


@pytest.mark.unit
def test_http_proxy_transport_passes_configured_handshake_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(_transport, "ensure_http_server", lambda *_args, **_kwargs: "http://127.0.0.1:8100/mcp")

    async def fake_run_stdio_proxy(url: str, **kwargs: Any) -> None:
        captured["url"] = url
        captured.update(kwargs)

    monkeypatch.setattr(_transport, "run_stdio_proxy", fake_run_stdio_proxy)

    config = TRWConfig.model_validate(
        {
            "mcp_transport": "streamable-http",
            "mcp_proxy_handshake_timeout_seconds": 2.5,
            "mcp_proxy_request_timeout_seconds": 90.0,
        }
    )

    _transport._run_http_proxy_transport(config, structlog.get_logger(__name__), debug=False)

    assert captured == {
        "url": "http://127.0.0.1:8100/mcp",
        "handshake_timeout_seconds": 2.5,
        # PRD-FIX-106: the per-call request timeout is now forwarded too.
        "request_timeout_seconds": 90.0,
    }


@pytest.mark.unit
def test_transport_fallback_diagnostic_names_http_and_local_fallbacks() -> None:
    diagnostic = _transport.build_transport_fallback_diagnostic("proxy_connect_failed", TRWConfig())

    assert diagnostic["diagnostic_event"] == "trw_transport_fallback_diagnostic"
    assert diagnostic["http_proxy_url"] == "http://127.0.0.1:8100/mcp"
    assert "trw-mcp local status|learn|deliver" in str(diagnostic["operator_guidance"])


@pytest.mark.unit
def test_http_token_bucket_denies_when_burst_exhausted() -> None:
    now = 100.0
    bucket = TokenBucket(capacity=2, refill_per_second=1.0, now=lambda: now)

    assert bucket.allow() is True
    assert bucket.allow() is True
    assert bucket.allow() is False


@pytest.mark.unit
async def test_local_token_bucket_rejection_is_typed_and_diagnostic() -> None:
    sent: list[dict[str, Any]] = []
    downstream_called = False

    async def app(_scope: dict[str, Any], _receive: Any, _send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = LocalTokenBucketMiddleware(app, capacity=1, refill_per_second=0.1)
    assert middleware.bucket.allow() is True

    await middleware(
        {"type": "http", "path": "/mcp", "client": ("127.0.0.1", 12345)},
        lambda: asyncio.sleep(0, result={"type": "http.request"}),
        send,
    )

    assert downstream_called is False
    assert sent[0]["status"] == 429
    assert (b"content-type", b"application/json") in sent[0]["headers"]
    body = json.loads(sent[1]["body"].decode("utf-8"))
    assert body == {
        "error": "local_mcp_rate_limit_exceeded",
        "detail": "Local MCP HTTP rate limit exceeded.",
        "retry_after": 1,
    }


@pytest.mark.unit
def test_http_transport_kwargs_include_origin_guard_and_rate_limit() -> None:
    config = TRWConfig(mcp_http_rate_limit_capacity=5, mcp_http_rate_limit_refill_per_second=1.0)

    kwargs = _transport._http_transport_kwargs(config)

    middleware = kwargs["middleware"]
    assert len(middleware) >= 1
    assert any(getattr(item, "cls", None).__name__ == "LocalTokenBucketMiddleware" for item in middleware)


@pytest.mark.unit
def test_config_runtime_reload_boundary_is_config_only() -> None:
    old = TRWConfig(mcp_http_rate_limit_capacity=120)
    new = TRWConfig(mcp_http_rate_limit_capacity=60)

    summary = _transport.summarize_runtime_reload(old, new)

    assert summary["changed_fields"] == ["mcp_http_rate_limit_capacity"]
    assert summary["requires_source_restart"] is False
    assert summary["boundary"] == "config_only_no_python_hot_reload"
