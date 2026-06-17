"""Session-isolation tests for post-compaction ceremony gate."""

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
    FakeRequestContext,
    FakeToolResult,
    _seed_compaction_marker,
    _text,
    middleware,  # noqa: F401
    session_ctx,  # noqa: F401
)
from trw_mcp.middleware.ceremony import CeremonyMiddleware, is_session_active


class TestCompactionGate:
    """Tests for the post-compaction gate that blocks trw_* tools."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_remains_per_session_after_sibling_recovers(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """A sibling session that already owes recovery stays blocked until its own start succeeds."""
        trw_dir = _seed_compaction_marker(tmp_path)
        session_a = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        session_b = FakeContext(request_context=FakeRequestContext(session_id="session-b"))
        start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"success": true, "errors": []}')],
            structured_content={"success": True, "errors": []},
        )
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start":
                return start_result
            return checkpoint_result

        blocked_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_b,
        )
        start_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_a,
        )

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            first_block = await middleware.on_call_tool(blocked_ctx, call_next)  # type: ignore[arg-type]
            start_out = await middleware.on_call_tool(start_ctx, call_next)  # type: ignore[arg-type]
            second_block = await middleware.on_call_tool(blocked_ctx, call_next)  # type: ignore[arg-type]

        assert first_block.structured_content is not None
        assert first_block.structured_content["error"] == "session_start_required"
        assert start_out.structured_content == {"success": True, "errors": []}
        assert is_session_active("session-a")
        assert not is_session_active("session-b")
        assert call_names == ["trw_session_start"]
        assert second_block.structured_content is not None
        assert second_block.structured_content["error"] == "session_start_required"
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_known_sibling_session_stays_blocked_after_other_session_clears_marker(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """A known sibling session cannot bypass recovery just because another session cleared the file."""
        session_a = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        session_b = FakeContext(request_context=FakeRequestContext(session_id="session-b"))
        initial_start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
        recovered_start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"success": true, "errors": []}')],
            structured_content={"success": True, "errors": []},
        )
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start" and len(call_names) == 1:
                return initial_start_result
            if ctx.message.name == "trw_session_start":
                return recovered_start_result
            return checkpoint_result

        start_a_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_a,
        )
        checkpoint_b_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_b,
        )

        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            await middleware.on_call_tool(start_a_ctx, call_next)  # type: ignore[arg-type]
            await middleware.on_call_tool(
                FakeMiddlewareContext(
                    message=FakeMessage(name="Read"),
                    fastmcp_context=session_b,
                ),
                call_next,
            )
            _seed_compaction_marker(tmp_path)
            await middleware.on_call_tool(start_a_ctx, call_next)  # type: ignore[arg-type]
            blocked_out = await middleware.on_call_tool(checkpoint_b_ctx, call_next)  # type: ignore[arg-type]

        assert blocked_out.structured_content is not None
        assert blocked_out.structured_content["error"] == "session_start_required"
        assert call_names == ["trw_session_start", "Read", "trw_session_start"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_inactive_without_compaction(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Normal flow: session started first, then tools work without interference."""
        start_result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success"}')])
        learn_result = FakeToolResult(content=[TextContent(type="text", text="learned")])
        build_result = FakeToolResult(content=[TextContent(type="text", text="built")])
        results = [start_result, learn_result, build_result]
        idx = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal idx
            result = results[idx]
            idx += 1
            return result

        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_learn"),
            fastmcp_context=session_ctx,
        )
        out2 = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]
        assert _text(out2.content[0]) == "learned"
        assert len(out2.content) == 1

        ctx3 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_build_check"),
            fastmcp_context=session_ctx,
        )
        out3 = await middleware.on_call_tool(ctx3, call_next)  # type: ignore[arg-type]
        assert _text(out3.content[0]) == "built"
        assert len(out3.content) == 1
