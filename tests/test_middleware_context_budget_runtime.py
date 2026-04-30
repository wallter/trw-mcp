"""Runtime middleware behavior tests for context budget middleware."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.types import TextContent

from tests._test_middleware_context_budget_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeRequestContext,
    FakeToolResult,
    _clean_state,
)
from trw_mcp.middleware.context_budget import ContextBudgetMiddleware, get_turn_count


class TestTurnTracking:
    """Tests for per-session turn count management."""

    @pytest.fixture
    def middleware(self) -> ContextBudgetMiddleware:
        return ContextBudgetMiddleware()

    @pytest.mark.asyncio
    async def test_turn_count_increments(self, middleware: ContextBudgetMiddleware) -> None:
        """3 sequential calls increment count to 3."""
        result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-1")
        fake_ctx = FakeContext(request_context=req_ctx)

        for _ in range(3):
            ctx = FakeMiddlewareContext(
                message=FakeMessage(name="trw_status"),
                fastmcp_context=fake_ctx,
            )
            await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert get_turn_count("sess-1") == 3

    @pytest.mark.asyncio
    async def test_turn_count_per_session(self, middleware: ContextBudgetMiddleware) -> None:
        """Different session IDs are tracked independently."""
        result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return result

        for sid in ("sess-a", "sess-b", "sess-a"):
            ctx = FakeMiddlewareContext(
                message=FakeMessage(name="trw_status"),
                fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id=sid)),
            )
            await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert get_turn_count("sess-a") == 2
        assert get_turn_count("sess-b") == 1


class TestRedundancyDetection:
    """Tests for identical response detection within sessions."""

    @pytest.fixture
    def middleware(self) -> ContextBudgetMiddleware:
        return ContextBudgetMiddleware()

    def _make_ctx(self, tool_name: str, session_id: str = "sess-1") -> FakeMiddlewareContext:
        return FakeMiddlewareContext(
            message=FakeMessage(name=tool_name),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id=session_id),
            ),
        )

    @pytest.mark.asyncio
    async def test_identical_response_detected(self, middleware: ContextBudgetMiddleware) -> None:
        """Same tool, same output on second call returns placeholder."""

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="same output")])

        ctx1 = self._make_ctx("trw_status")
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = self._make_ctx("trw_status")
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]

        assert len(out.content) == 1
        assert "No changes since turn" in out.content[0].text

    @pytest.mark.asyncio
    async def test_different_response_passes(self, middleware: ContextBudgetMiddleware) -> None:
        """Same tool, different output passes through."""
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return FakeToolResult(content=[TextContent(type="text", text=f"output-{call_count}")])

        ctx1 = self._make_ctx("trw_status")
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = self._make_ctx("trw_status")
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]

        assert "No changes" not in out.content[0].text

    @pytest.mark.asyncio
    async def test_redundancy_per_tool(self, middleware: ContextBudgetMiddleware) -> None:
        """Tool A and tool B tracked separately."""

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="shared output")])

        ctx_a1 = self._make_ctx("tool_a")
        await middleware.on_call_tool(ctx_a1, call_next)  # type: ignore[arg-type]

        ctx_b1 = self._make_ctx("tool_b")
        out = await middleware.on_call_tool(ctx_b1, call_next)  # type: ignore[arg-type]

        assert "No changes" not in out.content[0].text

    @pytest.mark.asyncio
    async def test_redundancy_per_session(self, middleware: ContextBudgetMiddleware) -> None:
        """Different sessions tracked separately."""

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="same")])

        ctx1 = self._make_ctx("trw_status", session_id="sess-1")
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = self._make_ctx("trw_status", session_id="sess-2")
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]

        assert "No changes" not in out.content[0].text


class TestIntegration:
    """Integration tests for middleware behavior with various content types."""

    @pytest.fixture
    def middleware(self) -> ContextBudgetMiddleware:
        return ContextBudgetMiddleware()

    @pytest.mark.asyncio
    async def test_non_text_content_passthrough(self, middleware: ContextBudgetMiddleware) -> None:
        """Non-TextContent blocks (e.g. ImageContent) pass through unchanged."""
        fake_image = MagicMock()
        fake_image.type = "image"
        result = FakeToolResult(content=[fake_image])

        async def call_next(_ctx: Any) -> Any:
            return result

        from trw_mcp.middleware.context_budget import _turn_counts

        _turn_counts["sess-img"] = 20
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id="sess-img")),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert out.content[0] is fake_image

    @pytest.mark.asyncio
    async def test_disabled_via_config(
        self,
        middleware: ContextBudgetMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """observation_masking=False means all responses pass through unchanged."""
        from trw_mcp.models.config import TRWConfig

        mock_config = TRWConfig(observation_masking=False)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: mock_config)

        long_text = "x" * 600

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text=long_text)])

        from trw_mcp.middleware.context_budget import _turn_counts

        _turn_counts["sess-disabled"] = 50
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id="sess-disabled"),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert out.content[0].text == long_text

    @pytest.mark.asyncio
    async def test_fail_open_on_error(
        self,
        middleware: ContextBudgetMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If compression raises, original content is preserved."""
        monkeypatch.setattr(
            "trw_mcp.middleware.context_budget.ContextBudgetMiddleware._apply_masking",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        original_text = "important data"

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text=original_text)])

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id="sess-err"),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert out.content[0].text == original_text

    @pytest.mark.asyncio
    async def test_no_context_passes_through(self, middleware: ContextBudgetMiddleware) -> None:
        """When fastmcp_context is None, middleware does nothing."""
        result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=None,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert out.content[0].text == "ok"
        assert get_turn_count("anything") == 0

    @pytest.mark.asyncio
    async def test_compression_applied_when_text_changes(
        self, middleware: ContextBudgetMiddleware
    ) -> None:
        """When compression actually changes the text, the block is replaced."""
        payload = json.dumps(
            {
                "summary": "done",
                "metadata": {"internal": True},
            }
        )
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        from trw_mcp.middleware.context_budget import _turn_counts

        session_id = "sess-compress"
        _turn_counts[session_id] = 11
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id=session_id),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        parsed = json.loads(out.content[0].text)
        assert "metadata" not in parsed
        assert parsed["summary"] == "done"

    @pytest.mark.asyncio
    async def test_config_exception_uses_fallback_thresholds(
        self, middleware: ContextBudgetMiddleware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When get_config() raises, fallback thresholds (compact=10, minimal=30) are used."""
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
        )

        payload = json.dumps({"summary": "ok", "metadata": {"foo": "bar"}})

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text=payload)])

        from trw_mcp.middleware.context_budget import _turn_counts

        session_id = "sess-fallback"
        _turn_counts[session_id] = 11
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id=session_id),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        parsed = json.loads(out.content[0].text)
        assert "metadata" not in parsed
