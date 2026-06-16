"""Regression tests for PRD-FIX-106 stdio↔HTTP proxy per-call request timeout.

Incident: a single ``trw_learn`` MCP call hung 7+ hours after the shared HTTP
server was restarted. Root cause: the proxy forwarded ``tools/call`` through an
MCP ``ClientSession`` with NO ``read_timeout_seconds`` at either the session
constructor (``_proxy.py`` ``ClientSession(read, write)``) or the per-call
``session.call_tool(...)``. Both ``None`` values reach the SDK's
``send_request`` (``mcp/shared/session.py``) where ``timeout = None`` →
``anyio.fail_after(None)`` is an UNBOUNDED wait, so an orphaned in-flight
response blocks forever.

These tests prove (a) the unbounded-wait class is closed by a finite timeout and
(b) ``run_stdio_proxy`` threads its ``request_timeout_seconds`` into BOTH SDK
layers. Pure unit — no live server, no socket, no filesystem I/O.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _transport


@pytest.mark.unit
def test_config_default_request_timeout_is_sixty_seconds() -> None:
    config = TRWConfig()

    assert config.mcp_proxy_request_timeout_seconds == 60.0


@pytest.mark.unit
def test_config_request_timeout_is_overridable() -> None:
    config = TRWConfig.model_validate({"mcp_proxy_request_timeout_seconds": 120.0})

    assert config.mcp_proxy_request_timeout_seconds == 120.0


@pytest.mark.unit
async def test_unbounded_wait_class_is_closed_by_finite_timeout() -> None:
    """A never-resolving await, wrapped in the SDK's bounded pattern with a
    FINITE timeout, raises within the budget — proving the fix's mechanism.

    Mirrors mcp/shared/session.py send_request:
        with anyio.fail_after(timeout): await receive()
    With ``timeout=None`` (the pre-fix state) this would block forever; with a
    finite budget it raises promptly.
    """
    import anyio

    request_timeout = timedelta(seconds=0.05)

    async def never_resolves() -> None:
        await asyncio.Event().wait()  # never set -> blocks forever absent a bound

    with pytest.raises(TimeoutError):
        with anyio.fail_after(request_timeout.total_seconds()):
            await never_resolves()


@pytest.mark.unit
async def test_run_stdio_proxy_threads_finite_timeout_into_session_and_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_stdio_proxy(request_timeout_seconds=N)`` must construct the
    ``ClientSession`` with ``read_timeout_seconds=timedelta(seconds=N)`` AND
    forward that same finite timeout on every ``call_tool``.
    """
    from mcp.client import session as mcp_session_mod
    from mcp.client import streamable_http as mcp_http_mod
    from mcp.server import stdio as mcp_stdio_mod

    captured: dict[str, Any] = {}

    # --- Fake streamable_http_client: yields dummy read/write streams. ---
    class _FakeHttpCtx:
        async def __aenter__(self) -> tuple[object, object, object]:
            return (object(), object(), object())

        async def __aexit__(self, *_exc: object) -> None:
            return None

    def fake_streamable_http_client(url: str) -> _FakeHttpCtx:
        captured["url"] = url
        return _FakeHttpCtx()

    monkeypatch.setattr(mcp_http_mod, "streamable_http_client", fake_streamable_http_client)

    # --- Fake ClientSession: captures read_timeout_seconds + call_tool args. ---
    class _FakeResult:
        def __init__(self, items: list[object]) -> None:
            self.tools = items
            self.resources = items
            self.prompts = items

    class _FakeSession:
        def __init__(self, _read: object, _write: object, *, read_timeout_seconds: Any = None) -> None:
            captured["session_read_timeout_seconds"] = read_timeout_seconds

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> _FakeResult:
            return _FakeResult([])

        async def list_resources(self) -> _FakeResult:
            return _FakeResult([])

        async def list_prompts(self) -> _FakeResult:
            return _FakeResult([])

        async def call_tool(self, name: str, arguments: Any = None, *, read_timeout_seconds: Any = None) -> object:
            captured["call_tool_read_timeout_seconds"] = read_timeout_seconds
            return object()

    monkeypatch.setattr(mcp_session_mod, "ClientSession", _FakeSession)

    # --- Fake stdio_server + a proxy.run that exercises handle_call_tool then exits. ---
    class _FakeStdioCtx:
        async def __aenter__(self) -> tuple[object, object]:
            return (object(), object())

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(mcp_stdio_mod, "stdio_server", lambda: _FakeStdioCtx())

    # Patch the Server so proxy.run drives the registered call-tool handler once.
    # ``_proxy.py`` does ``from mcp.server import Server`` — patch that binding.
    import mcp.server as mcp_server_mod

    class _FakeServer:
        def __init__(self, _name: str) -> None:
            self._call_tool_handler: Any = None

        def list_tools(self) -> Any:
            return lambda fn: fn

        def call_tool(self, *_a: object, **_kw: object) -> Any:
            def _decorator(fn: Any) -> Any:
                self._call_tool_handler = fn
                return fn

            return _decorator

        def list_resources(self) -> Any:
            return lambda fn: fn

        def read_resource(self) -> Any:
            return lambda fn: fn

        def list_prompts(self) -> Any:
            return lambda fn: fn

        def get_prompt(self) -> Any:
            return lambda fn: fn

        def create_initialization_options(self) -> object:
            return object()

        async def run(self, _r: object, _w: object, _opts: object) -> None:
            # Drive the forwarded call once so call_tool's timeout is exercised.
            assert self._call_tool_handler is not None
            await self._call_tool_handler("trw_learn", {"summary": "x"})

    monkeypatch.setattr(mcp_server_mod, "Server", _FakeServer)

    from trw_mcp.server._proxy import run_stdio_proxy

    await run_stdio_proxy(
        "http://127.0.0.1:8100/mcp",
        max_retries=1,
        handshake_timeout_seconds=5.0,
        request_timeout_seconds=42.0,
    )

    assert captured["session_read_timeout_seconds"] == timedelta(seconds=42.0)
    assert captured["call_tool_read_timeout_seconds"] == timedelta(seconds=42.0)


@pytest.mark.unit
def test_http_proxy_transport_forwards_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_run_http_proxy_transport`` must forward
    ``request_timeout_seconds=config.mcp_proxy_request_timeout_seconds`` into
    ``run_stdio_proxy`` alongside the handshake timeout.
    """
    captured: dict[str, Any] = {}

    monkeypatch.setattr(_transport, "ensure_http_server", lambda *_a, **_k: "http://127.0.0.1:8100/mcp")

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
        "request_timeout_seconds": 90.0,
    }
