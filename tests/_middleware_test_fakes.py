"""Shared FastMCP-shaped fakes for middleware tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FakeRequestContext:
    session_id: str = "test-session-1"


@dataclass
class FakeContext:
    request_context: FakeRequestContext | None = None

    @property
    def session_id(self) -> str:
        if self.request_context is None:
            raise RuntimeError("No request context")
        return self.request_context.session_id


@dataclass
class FakeMessage:
    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class FakeMiddlewareContext:
    message: FakeMessage
    fastmcp_context: FakeContext | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FakeToolResult:
    content: list[Any]
