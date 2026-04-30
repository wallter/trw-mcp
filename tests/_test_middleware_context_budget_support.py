"""Shared support for context budget middleware test splits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from trw_mcp.middleware.context_budget import reset_state


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset module-level session state before each test."""
    reset_state()


@dataclass
class FakeRequestContext:
    """Minimal request context stub."""

    session_id: str = "test-session-1"


@dataclass
class FakeContext:
    """Minimal FastMCP Context stub with session_id."""

    request_context: FakeRequestContext | None = None

    @property
    def session_id(self) -> str:
        if self.request_context is None:
            raise RuntimeError("No request context")
        return self.request_context.session_id


@dataclass
class FakeMessage:
    """Minimal CallToolRequestParams stub."""

    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class FakeMiddlewareContext:
    """Minimal MiddlewareContext stub."""

    message: FakeMessage
    fastmcp_context: FakeContext | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FakeToolResult:
    """Minimal ToolResult stub with mutable content list."""

    content: list[Any]
