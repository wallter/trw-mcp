"""Local HTTP MCP token-bucket middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]


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
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"retry-after", b"1"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"local MCP rate limit exceeded"})


__all__ = ["LocalTokenBucketMiddleware", "TokenBucket"]
