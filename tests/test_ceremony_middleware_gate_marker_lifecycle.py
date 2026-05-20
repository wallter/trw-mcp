"""Marker lifecycle tests for post-compaction ceremony gate."""

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
from trw_mcp.middleware.ceremony import CeremonyMiddleware, is_session_active


class TestCompactionGate:
    """Tests for the post-compaction gate that blocks trw_* tools."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_only_session_start_clears_real_compaction_marker(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """Blocked or non-ceremony calls do not clear the marker; session_start does."""
        trw_dir = _seed_compaction_marker(tmp_path)
        read_result = FakeToolResult(content=[TextContent(type="text", text="read ok")])
        start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start":
                return start_result
            return read_result

        read_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=session_ctx,
        )
        start_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            await middleware.on_call_tool(read_ctx, call_next)  # type: ignore[arg-type]
            assert (trw_dir / "context" / "pre_compact_state.json").exists()
            await middleware.on_call_tool(start_ctx, call_next)  # type: ignore[arg-type]

        assert call_names == ["Read", "trw_session_start"]
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_clears_after_session_start(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """After session_start, trw_checkpoint passes through normally."""
        start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        trw_dir = _seed_compaction_marker(tmp_path)
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_result
            return checkpoint_result

        ctx1 = FakeMiddlewareContext(
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
            await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

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
    async def test_failed_session_start_does_not_clear_gate(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """Unsuccessful session_start must not activate the session or clear the marker."""
        trw_dir = _seed_compaction_marker(tmp_path)
        start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"success": false, "errors": ["recall failed"]}')],
            structured_content={"success": False, "errors": ["recall failed"]},
        )
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start":
                return start_result
            return checkpoint_result

        start_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        checkpoint_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            start_out = await middleware.on_call_tool(start_ctx, call_next)  # type: ignore[arg-type]
            checkpoint_out = await middleware.on_call_tool(checkpoint_ctx, call_next)  # type: ignore[arg-type]

        assert start_out.structured_content == {"success": False, "errors": ["recall failed"]}
        assert call_names == ["trw_session_start"]
        assert not is_session_active("test-session-gate")
        assert (trw_dir / "context" / "pre_compact_state.json").exists()
        assert checkpoint_out.structured_content is not None
        assert checkpoint_out.structured_content["error"] == "session_start_required"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_reblocks_active_session_after_later_compaction(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """An already-started session must recover again after a new compaction event."""
        trw_dir = tmp_path / ".trw"
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"success": true, "errors": []}')],
            structured_content={"success": True, "errors": []},
        )
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start":
                return start_result
            return checkpoint_result

        initial_start_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        checkpoint_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            await middleware.on_call_tool(initial_start_ctx, call_next)  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path)
            blocked_out = await middleware.on_call_tool(checkpoint_ctx, call_next)  # type: ignore[arg-type]
            recovered_out = await middleware.on_call_tool(initial_start_ctx, call_next)  # type: ignore[arg-type]
            final_out = await middleware.on_call_tool(checkpoint_ctx, call_next)  # type: ignore[arg-type]

        assert blocked_out.structured_content is not None
        assert blocked_out.structured_content["error"] == "session_start_required"
        assert recovered_out.structured_content == {"success": True, "errors": []}
        assert final_out.structured_content is None
        assert [_text(block) for block in final_out.content] == ["checkpoint ok"]
        assert call_names == ["trw_session_start", "trw_session_start", "trw_checkpoint"]
