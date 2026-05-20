"""Blocking behavior tests for post-compaction ceremony gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from tests._test_ceremony_middleware_gate_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeToolResult,
    _seed_compaction_marker,
    _text,
    middleware,  # noqa: F401
    session_ctx,  # noqa: F401
)
from trw_mcp.middleware.ceremony import CeremonyMiddleware, is_session_active, reset_state


class TestCompactionGate:
    """Tests for the post-compaction gate that blocks trw_* tools."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_blocks_checkpoint(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """trw_checkpoint called without session_start returns error dict."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_count = 0
        _seed_compaction_marker(tmp_path)

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

        assert call_count == 0, "call_next should not be invoked when gate blocks"
        assert len(out.content) == 1
        first = out.content[0]
        assert isinstance(first, TextContent)
        assert "trw_session_start()" in first.text
        assert out.structured_content is not None
        assert out.structured_content["error"] == "session_start_required"
        assert out.structured_content["tool_attempted"] == "trw_checkpoint"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_reads_real_marker_file(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The hard gate is driven by the pre_compact_state.json marker on disk."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        trw_dir = _seed_compaction_marker(tmp_path)
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 0
        assert out.structured_content is not None
        assert out.structured_content["error"] == "session_start_required"
        assert (trw_dir / "context" / "pre_compact_state.json").exists()

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
            patch(
                "trw_mcp.middleware.ceremony._clear_compaction_gate_safe",
                side_effect=lambda: (trw_dir / "context" / "pre_compact_state.json").unlink(),
            ),
        ):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert len(out.content) == 1
        assert _text(out.content[0]) == "session started"
        assert is_session_active("test-session-gate")
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

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
        tool_result = FakeToolResult(content=[TextContent(type="text", text="file contents")])
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=session_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 1, "Non-trw tool should still execute"
        texts = [b.text for b in out.content if isinstance(b, TextContent)]
        assert "file contents" in texts

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_blocks_multiple_trw_tools(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """Various trw_* tools should all be blocked when session not started."""
        _seed_compaction_marker(tmp_path)
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
            reset_state()
            tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

            async def call_next(_ctx: Any) -> Any:
                return tool_result

            ctx = FakeMiddlewareContext(
                message=FakeMessage(name=tool_name),
                fastmcp_context=session_ctx,
            )
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

            assert _text(out.content[0]) == "ok", f"{tool_name} should pass through without blocking"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_error_response_structure(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The error response contains expected structured fields."""
        _seed_compaction_marker(tmp_path)
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
