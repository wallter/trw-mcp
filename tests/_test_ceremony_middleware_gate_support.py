"""Shared support for split ceremony middleware gate tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.ceremony import CeremonyMiddleware, reset_state

if TYPE_CHECKING:
    from mcp.types import ContentBlock


def _text(block: ContentBlock) -> str:
    """Extract text from a content block with type narrowing."""
    assert isinstance(block, TextContent)
    return block.text


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset module-level session state before each test."""
    reset_state()


@dataclass
class FakeRequestContext:
    """Minimal request context stub."""

    session_id: str = "test-session-gate"


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
    timestamp: datetime = datetime.now(timezone.utc)


@dataclass
class FakeToolResult:
    """Minimal ToolResult stub with mutable content list."""

    content: list[Any]
    structured_content: dict[str, Any] | None = None


@pytest.fixture
def middleware() -> CeremonyMiddleware:
    return CeremonyMiddleware()


@pytest.fixture
def session_ctx() -> FakeContext:
    return FakeContext(request_context=FakeRequestContext())


def _seed_compaction_marker(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    context_dir = trw_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "pre_compact_state.json").write_text("{}", encoding="utf-8")
    return trw_dir
