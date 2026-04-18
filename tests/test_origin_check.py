"""Unit tests for the Origin-guard ASGI middleware.

The middleware protects the shared HTTP MCP server on 127.0.0.1 against
cross-origin browser fetches. Server-to-server HTTP clients (the stdio
proxy, opencode, Claude Desktop) do not send Origin and must always pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from trw_mcp.server._origin_check import (
    OriginGuardMiddleware,
    _origin_allowed,
    origin_check_enabled,
)

pytestmark = pytest.mark.unit


# ── Helpers ─────────────────────────────────────────────────────────────


class _AppRecorder:
    """Dummy ASGI app that records whether it was invoked."""

    def __init__(self) -> None:
        self.called = False
        self.last_scope: dict[str, Any] | None = None

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        self.called = True
        self.last_scope = scope
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class _SendCollector:
    """Collects ASGI send messages so tests can inspect the response."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        for m in self.messages:
            if m.get("type") == "http.response.start":
                value = m.get("status")
                return int(value) if value is not None else None
        return None

    @property
    def body(self) -> bytes:
        chunks = [m.get("body", b"") for m in self.messages if m.get("type") == "http.response.body"]
        return b"".join(chunks)


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers or [],
    }


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


# ── _origin_allowed pure-function tests ─────────────────────────────────


class TestOriginAllowed:
    def test_null_origin_allowed(self) -> None:
        assert _origin_allowed("null") is True

    def test_loopback_ipv4_allowed(self) -> None:
        assert _origin_allowed("http://127.0.0.1:8100") is True
        assert _origin_allowed("http://127.0.0.1") is True
        assert _origin_allowed("https://127.0.0.1:8443") is True

    def test_localhost_allowed(self) -> None:
        assert _origin_allowed("http://localhost:3000") is True
        assert _origin_allowed("http://localhost") is True

    def test_loopback_ipv6_allowed(self) -> None:
        # IPv6 loopback in either bracketed or bare form
        assert _origin_allowed("http://[::1]:8100") is True

    def test_external_origin_rejected(self) -> None:
        assert _origin_allowed("https://evil.com") is False
        assert _origin_allowed("http://attacker.local") is False
        assert _origin_allowed("https://192.168.1.5:8080") is False

    def test_spoof_prefix_rejected(self) -> None:
        # Host that merely starts with "localhost" but isn't loopback
        assert _origin_allowed("http://localhost.evil.com") is False
        assert _origin_allowed("http://127.0.0.1.evil.com") is False


# ── Middleware behavior ────────────────────────────────────────────────


class TestOriginGuardMiddleware:
    async def test_no_origin_header_passes(self) -> None:
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        await mw(_http_scope(headers=[]), _noop_receive, send)

        assert app.called is True
        assert send.status == 200

    async def test_loopback_origin_passes(self) -> None:
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        await mw(
            _http_scope(headers=[(b"origin", b"http://127.0.0.1:8100")]),
            _noop_receive,
            send,
        )

        assert app.called is True
        assert send.status == 200

    async def test_localhost_origin_passes(self) -> None:
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        await mw(
            _http_scope(headers=[(b"origin", b"http://localhost:6274")]),  # MCP inspector
            _noop_receive,
            send,
        )

        assert app.called is True

    async def test_null_origin_passes(self) -> None:
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        await mw(
            _http_scope(headers=[(b"origin", b"null")]),
            _noop_receive,
            send,
        )

        assert app.called is True

    async def test_cross_origin_rejected_403(self) -> None:
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        await mw(
            _http_scope(headers=[(b"origin", b"https://evil.com")]),
            _noop_receive,
            send,
        )

        assert app.called is False, "downstream app must not be invoked on reject"
        assert send.status == 403
        assert b"cross-origin" in send.body.lower()
        assert b"evil.com" in send.body

    async def test_spoofed_localhost_prefix_rejected(self) -> None:
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        await mw(
            _http_scope(headers=[(b"origin", b"http://localhost.evil.com")]),
            _noop_receive,
            send,
        )

        assert app.called is False
        assert send.status == 403

    async def test_non_http_scope_passes_unmodified(self) -> None:
        """Lifespan/websocket scopes must not be filtered."""
        app = _AppRecorder()
        mw = OriginGuardMiddleware(app)
        send = _SendCollector()

        lifespan_scope: dict[str, Any] = {"type": "lifespan"}
        await mw(lifespan_scope, _noop_receive, send)

        assert app.called is True
        assert app.last_scope is lifespan_scope


# ── Kill switch ────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRW_MCP_DISABLE_ORIGIN_CHECK", raising=False)
        assert origin_check_enabled() is True

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
    def test_disabled_by_env(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("TRW_MCP_DISABLE_ORIGIN_CHECK", value)
        assert origin_check_enabled() is False

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "random"])
    def test_other_values_leave_enabled(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("TRW_MCP_DISABLE_ORIGIN_CHECK", value)
        assert origin_check_enabled() is True


# ── Transport wiring ───────────────────────────────────────────────────


class TestTransportWiring:
    """Verify `_http_transport_kwargs` returns middleware by default and
    drops it when the kill switch is set."""

    def test_wiring_default_includes_middleware(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRW_MCP_DISABLE_ORIGIN_CHECK", raising=False)
        from trw_mcp.server._transport import _http_transport_kwargs

        kwargs = _http_transport_kwargs()

        assert "middleware" in kwargs
        mw_list = kwargs["middleware"]
        assert isinstance(mw_list, list)
        assert len(mw_list) == 1
        # The Middleware wrapper carries the class it will instantiate
        assert mw_list[0].cls is OriginGuardMiddleware  # type: ignore[attr-defined]

    def test_wiring_empty_when_kill_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_MCP_DISABLE_ORIGIN_CHECK", "1")
        from trw_mcp.server._transport import _http_transport_kwargs

        assert _http_transport_kwargs() == {}
