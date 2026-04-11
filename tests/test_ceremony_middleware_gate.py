"""Tests for post-compaction ceremony gate — blocks trw_* tools until session_start.

PRD-CORE-098 FR06: When session is not started, trw_* tools (except
trw_session_start) return an error response instead of executing.
Non-trw_* tools are unaffected by the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from mcp.types import TextContent

if TYPE_CHECKING:
    from mcp.types import ContentBlock


def _text(block: ContentBlock) -> str:
    """Extract text from a content block with type narrowing."""
    assert isinstance(block, TextContent)
    return block.text

from trw_mcp.middleware.ceremony import (
    CeremonyMiddleware,
    is_session_active,
    reset_state,
)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset module-level session state before each test."""
    reset_state()


# --- Fakes mirroring test_middleware_ceremony.py patterns ---


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


def _seed_compaction_marker(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    context_dir = trw_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "pre_compact_state.json").write_text("{}", encoding="utf-8")
    return trw_dir


# --- Gate Tests ---


class TestCompactionGate:
    """Tests for the post-compaction gate that blocks trw_* tools."""

    @pytest.fixture
    def middleware(self) -> CeremonyMiddleware:
        return CeremonyMiddleware()

    @pytest.fixture
    def session_ctx(self) -> FakeContext:
        return FakeContext(request_context=FakeRequestContext())

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_blocks_checkpoint(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """trw_checkpoint called without session_start returns error dict."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_count = 0
        trw_dir = _seed_compaction_marker(tmp_path)

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        # The gate should block execution — call_next should NOT be called
        assert call_count == 0, "call_next should not be invoked when gate blocks"

        # Result should contain exactly one TextContent plus structured dict payload.
        assert len(out.content) == 1
        first = out.content[0]
        assert isinstance(first, TextContent)
        assert "trw_session_start()" in first.text
        assert out.structured_content is not None
        assert out.structured_content["error"] == "session_start_required"
        assert out.structured_content["tool_attempted"] == "trw_checkpoint"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_allows_session_start(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """trw_session_start always passes through even without prior session."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="session started")])
        trw_dir = _seed_compaction_marker(tmp_path)

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        with (
            patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True),
            patch("trw_mcp.middleware.ceremony._clear_compaction_gate_safe", side_effect=lambda: (trw_dir / "context" / "pre_compact_state.json").unlink()),
        ):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        # session_start should pass through normally
        assert len(out.content) == 1
        assert _text(out.content[0]) == "session started"
        # And session should now be active
        assert is_session_active("test-session-gate")
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_clears_after_session_start(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """After session_start, trw_checkpoint passes through normally."""
        start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
        checkpoint_result = FakeToolResult(
            content=[TextContent(type="text", text="checkpoint ok")]
        )
        trw_dir = _seed_compaction_marker(tmp_path)

        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_result
            return checkpoint_result

        # Step 1: call trw_session_start
        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        with (
            patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True),
            patch("trw_mcp.middleware.ceremony._clear_compaction_gate_safe", side_effect=lambda: (trw_dir / "context" / "pre_compact_state.json").unlink()),
        ):
            await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        # Step 2: call trw_checkpoint — should now pass through
        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=False):
            out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]

        assert call_count == 2, "call_next should be invoked for both calls"
        assert len(out.content) == 1
        assert _text(out.content[0]) == "checkpoint ok"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_inactive_without_compaction(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Normal flow: session started first, then tools work without interference."""
        start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
        learn_result = FakeToolResult(content=[TextContent(type="text", text="learned")])
        build_result = FakeToolResult(content=[TextContent(type="text", text="built")])

        results = [start_result, learn_result, build_result]
        idx = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal idx
            result = results[idx]
            idx += 1
            return result

        # Start session
        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        # trw_learn — should work normally
        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_learn"),
            fastmcp_context=session_ctx,
        )
        out2 = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]
        assert _text(out2.content[0]) == "learned"
        assert len(out2.content) == 1  # No warning prepended

        # trw_build_check — should also work normally
        ctx3 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_build_check"),
            fastmcp_context=session_ctx,
        )
        out3 = await middleware.on_call_tool(ctx3, call_next)  # type: ignore[arg-type]
        assert _text(out3.content[0]) == "built"
        assert len(out3.content) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_trw_tools_are_not_blocked_without_compaction(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Without a compaction marker, trw_* tools still execute."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 1
        texts = [block.text for block in out.content if isinstance(block, TextContent)]
        assert "checkpoint ok" in texts

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_does_not_affect_non_trw_tools(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Non-trw_* tools (e.g. Read, Bash) should never be blocked by the gate."""
        tool_result = FakeToolResult(
            content=[TextContent(type="text", text="file contents")]
        )
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        # Session NOT started, but tool is not a trw_* tool
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=session_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        # Should pass through (with warning prepended, but NOT blocked)
        assert call_count == 1, "Non-trw tool should still execute"
        # The existing warning behavior prepends a warning for non-ceremony sessions
        texts = [b.text for b in out.content if isinstance(b, TextContent)]
        assert "file contents" in texts

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_blocks_multiple_trw_tools(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """Various trw_* tools should all be blocked when session not started."""
        trw_dir = _seed_compaction_marker(tmp_path)
        blocked_tools = [
            "trw_checkpoint",
            "trw_learn",
            "trw_deliver",
            "trw_build_check",
            "trw_status",
            "trw_prd_create",
            "trw_prd_validate",
            "trw_init",
            "trw_recall",
        ]

        for tool_name in blocked_tools:
            call_count = 0
            tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

            async def call_next(_ctx: Any) -> Any:
                nonlocal call_count
                call_count += 1
                return tool_result

            ctx = FakeMiddlewareContext(
                message=FakeMessage(name=tool_name),
                fastmcp_context=session_ctx,
            )
            with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
                out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

            assert call_count == 0, f"{tool_name} should be blocked"
            assert out.structured_content is not None
            assert out.structured_content["error"] == "session_start_required", (
                f"{tool_name} should return session_start_required error"
            )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_allows_ceremony_tools_without_session(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Only trw_session_start passes through before the gate is cleared."""
        ceremony_tools = ["trw_session_start"]

        for tool_name in ceremony_tools:
            reset_state()  # Clean between iterations
            tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

            async def call_next(_ctx: Any) -> Any:
                return tool_result

            ctx = FakeMiddlewareContext(
                message=FakeMessage(name=tool_name),
                fastmcp_context=session_ctx,
            )
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

            assert _text(out.content[0]) == "ok", (
                f"{tool_name} should pass through without blocking"
            )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_error_response_structure(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The error response contains expected structured fields."""
        trw_dir = _seed_compaction_marker(tmp_path)

        tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_learn"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert out.structured_content is not None
        error_data = out.structured_content
        assert error_data["error"] == "session_start_required"
        assert "trw_session_start()" in error_data["message"]
        assert error_data["tool_attempted"] == "trw_learn"
