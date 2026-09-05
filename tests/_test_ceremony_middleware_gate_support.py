"""Shared support for split ceremony middleware gate tests."""

from __future__ import annotations

import json
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


#: The instant every seeded marker carries unless a test overrides it. A real
#: ISO timestamp, because the gate payload now reports the marker's own instant
#: (PRD-CORE-258-FR02): a fixture that wrote the bare two-character body ``{}``
#: — as this one did until 2026-09-04 — would land every gate test on the
#: ``unreadable`` branch by accident rather than on purpose.
SEEDED_MARKER_TS = "2026-09-04T21:49:47.123456+00:00"


def _seed_compaction_marker(tmp_path: Path, *, body: str | None = None, owner_pin_key: str = "") -> Path:
    """Write a pre-compaction marker under ``tmp_path/.trw``.

    ``body`` writes a raw document verbatim — the opt-in the one test that must
    exercise the ``unreadable`` branch uses. ``owner_pin_key`` names the marker's
    owner (PRD-CORE-258-FR10); the default is an OWNERLESS marker, which arms the
    whole generation exactly as the bundled PreCompact hook's marker does.
    """
    trw_dir = tmp_path / ".trw"
    context_dir = trw_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    if body is None:
        document: dict[str, object] = {"timestamp": SEEDED_MARKER_TS, "trigger": "mcp_tool"}
        if owner_pin_key:
            document["owner_pin_key"] = owner_pin_key
            document["owner_pid"] = 4242
        body = json.dumps(document)
    (context_dir / "pre_compact_state.json").write_text(body, encoding="utf-8")
    return trw_dir
