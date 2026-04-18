"""Origin-header validation middleware for the shared HTTP MCP server.

The shared HTTP transport (`http://127.0.0.1:8100/mcp`) is bound to loopback
and has no auth. The only realistic attack surface is a browser tab on the
developer's machine (a visited page, malicious extension, or local webapp)
issuing a cross-origin `fetch(...)` against the MCP endpoint — a
localhost confused-deputy.

This middleware rejects HTTP requests whose ``Origin`` header is set to an
origin outside a small loopback allowlist. Non-browser MCP clients (the
stdio proxy, Claude Desktop over HTTP, opencode, Cursor CLI, Codex CLI)
do not send ``Origin``; they pass through unchanged.

Kill switch: set ``TRW_MCP_DISABLE_ORIGIN_CHECK=1`` to disable entirely
(restores v0.45 behavior — use only for debugging).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
"""Hosts considered same-machine for Origin-allowlist purposes.

Ports and schemes are intentionally not matched — an attacker running
arbitrary code on a different localhost port implies an already-compromised
dev box, where an Origin check is not the last line of defense.
"""


class OriginGuardMiddleware:
    """Pure-ASGI middleware that rejects cross-origin browser requests.

    Rules:
      - Request has no ``Origin`` header → allow (server-to-server HTTP client).
      - ``Origin: null`` → allow (``file://`` pages, sandboxed iframes; these
        cannot hold cookies or CSRF state, so they are harmless here).
      - ``Origin`` host ∈ loopback allowlist → allow.
      - Any other ``Origin`` → 403.

    Pure ASGI (no ``BaseHTTPMiddleware``) to avoid buffering SSE responses
    emitted by the streamable-HTTP transport.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app
        self._log = structlog.get_logger(__name__)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        origin = _extract_origin(scope)
        if origin is not None and not _origin_allowed(origin):
            self._log.warning(
                "origin_rejected",
                origin=origin,
                path=scope.get("path"),
                method=scope.get("method"),
            )
            await _send_forbidden(send, origin)
            return

        await self._app(scope, receive, send)


def _extract_origin(scope: Scope) -> str | None:
    """Return the ``Origin`` header value, or None if absent."""
    for name, value in scope.get("headers", ()):
        if name == b"origin":
            try:
                return bytes(value).decode("latin-1")
            except (UnicodeDecodeError, TypeError):
                return None
    return None


def _origin_allowed(origin: str) -> bool:
    """Return True if the Origin is same-machine or an allowed sandbox marker."""
    if origin == "null":
        return True
    # Strip "scheme://" prefix and any trailing path, leaving ``host[:port]``.
    authority = origin.split("://", 1)[-1].split("/", 1)[0]
    # Bracketed IPv6: "[::1]:8100" → host "::1". Bare host: split on first ":".
    if authority.startswith("["):
        end = authority.find("]")
        host = authority[1:end] if end > 0 else authority
    else:
        host = authority.split(":", 1)[0]
    return host in _LOOPBACK_HOSTS


async def _send_forbidden(send: Send, origin: str) -> None:
    """Emit a minimal 403 ASGI response."""
    body = (
        f"403 Forbidden: cross-origin request from {origin!r} rejected "
        "by TRW MCP server. Set TRW_MCP_DISABLE_ORIGIN_CHECK=1 to bypass."
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def origin_check_enabled() -> bool:
    """Return True unless the kill-switch env var is set to a truthy value."""
    raw = os.environ.get("TRW_MCP_DISABLE_ORIGIN_CHECK", "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}
