"""Regression tests for PRD-FIX-089 stdio proxy handshake budgets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _transport
from trw_mcp.server._proxy import discover_proxy_capabilities


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
        }
    )

    _transport._run_http_proxy_transport(config, structlog.get_logger(__name__), debug=False)

    assert captured == {
        "url": "http://127.0.0.1:8100/mcp",
        "handshake_timeout_seconds": 2.5,
    }
