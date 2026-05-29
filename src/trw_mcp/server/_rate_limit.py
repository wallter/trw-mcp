"""Local HTTP MCP token-bucket middleware."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]

logger = structlog.get_logger(__name__)


class TokenBucket:
    """Small in-process token bucket for loopback HTTP safety."""

    def __init__(self, *, capacity: int, refill_per_second: float, now: Callable[[], float] | None = None) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be > 0")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._tokens = float(capacity)
        self._now = now or time.monotonic
        self._last_refill = self._now()

    def allow(self) -> bool:
        current = self._now()
        elapsed = max(0.0, current - self._last_refill)
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.refill_per_second)
        self._last_refill = current
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class LocalTokenBucketMiddleware:
    """ASGI middleware that rejects excessive local HTTP requests with 429.

    The middleware decides before handing the request to downstream handlers, so
    accepted streaming/SSE responses are never wrapped or buffered.
    """

    def __init__(self, app: ASGIApp, *, capacity: int, refill_per_second: float) -> None:
        self.app = app
        self.bucket = TokenBucket(capacity=capacity, refill_per_second=refill_per_second)

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http" or self.bucket.allow():
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_host = client[0] if isinstance(client, tuple) and client else "unknown"
        retry_after = 1
        logger.warning(
            "local_mcp_rate_limit_exceeded",
            component="LocalTokenBucketMiddleware",
            op="rate_limit",
            outcome="denied",
            path=scope.get("path", "unknown"),
            client_host=client_host,
            retry_after=retry_after,
            capacity=self.bucket.capacity,
            refill_per_second=self.bucket.refill_per_second,
        )
        body = json.dumps(
            {
                "error": "local_mcp_rate_limit_exceeded",
                "detail": "Local MCP HTTP rate limit exceeded.",
                "retry_after": retry_after,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["LocalTokenBucketMiddleware", "TokenBucket"]
