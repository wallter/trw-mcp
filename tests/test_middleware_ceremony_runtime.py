"""Runtime middleware behavior tests for trw_mcp.middleware.ceremony."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.types import TextContent

from tests._test_middleware_ceremony_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeRequestContext,
    FakeToolResult,
    _clean_state,  # noqa: F401 - importing registers the autouse fixture
)
from trw_mcp.middleware.ceremony import CEREMONY_WARNING, CeremonyMiddleware, is_session_active


class TestCeremonyMiddleware:
    """Tests for the CeremonyMiddleware on_call_tool behavior."""

    @pytest.fixture
    def middleware(self) -> CeremonyMiddleware:
        return CeremonyMiddleware()

    def test_clean_state_fixture_is_collected(self, request: pytest.FixtureRequest) -> None:
        assert "_clean_state" in request.fixturenames

    @pytest.mark.asyncio
    async def test_no_context_passes_through(self, middleware: CeremonyMiddleware) -> None:
        """When fastmcp_context is None (unit tests), middleware does nothing."""
        result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=None,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 1
        assert out.content[0].text == "ok"

    @pytest.mark.asyncio
    async def test_no_request_context_passes_through(self, middleware: CeremonyMiddleware) -> None:
        """When request_context is None, middleware does nothing."""
        result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return result

        fake_ctx = FakeContext(request_context=None)
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=fake_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 1

    @pytest.mark.asyncio
    async def test_ceremony_tool_marks_session_active(self, middleware: CeremonyMiddleware) -> None:
        """Calling a ceremony tool marks the session as active.

        Since commit f37b1063c the ceremony gate only activates on an explicit
        success payload (fail-closed detection in _session_start_succeeded); a
        successful session_start emits a JSON status payload.
        """
        result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success","detail":"started"}')])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-abc")
        fake_ctx = FakeContext(request_context=req_ctx)
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=fake_ctx,
        )
        await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert is_session_active("sess-abc")

    @pytest.mark.asyncio
    async def test_ceremony_tool_no_warning(self, middleware: CeremonyMiddleware) -> None:
        """Ceremony tools themselves should never get a warning prepended."""
        result = FakeToolResult(content=[TextContent(type="text", text="started")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-abc")
        fake_ctx = FakeContext(request_context=req_ctx)
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=fake_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 1
        assert out.content[0].text == "started"

    @pytest.mark.asyncio
    async def test_non_trw_tool_without_ceremony_gets_warning(
        self,
        middleware: CeremonyMiddleware,
    ) -> None:
        """Non-trw_* tool called before ceremony gets warning prepended.

        Note: trw_* tools are now blocked entirely by the post-compaction
        gate (PRD-CORE-098-FR06). Only non-trw tools get the warning.
        """
        result = FakeToolResult(content=[TextContent(type="text", text="status result")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-no-ceremony")
        fake_ctx = FakeContext(request_context=req_ctx)
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=fake_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 2
        assert out.content[0].text == CEREMONY_WARNING
        assert out.content[1].text == "status result"

    @pytest.mark.asyncio
    async def test_non_ceremony_tool_after_ceremony_no_warning(
        self,
        middleware: CeremonyMiddleware,
    ) -> None:
        """Non-exempt tool called AFTER ceremony has no warning.

        A successful session_start must report explicit success (commit
        f37b1063c fail-closed detection) for the session to be marked active.
        """
        start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"status":"success","detail":"started"}')]
        )
        status_result = FakeToolResult(content=[TextContent(type="text", text="status")])

        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_result
            return status_result

        req_ctx = FakeRequestContext(session_id="sess-active")
        fake_ctx = FakeContext(request_context=req_ctx)

        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=fake_ctx,
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=fake_ctx,
        )
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 1
        assert out.content[0].text == "status"

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self, middleware: CeremonyMiddleware) -> None:
        """Two parallel sessions are tracked independently."""

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="ok")])

        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id="sess-1")),
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id="sess-2")),
        )
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 2

    @pytest.mark.asyncio
    async def test_trw_init_marks_session_active(self, middleware: CeremonyMiddleware) -> None:
        """Without a compaction marker, trw_init still executes."""
        result = FakeToolResult(content=[TextContent(type="text", text="init")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-init")
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_init"),
            fastmcp_context=FakeContext(request_context=req_ctx),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert not is_session_active("sess-init")
        texts = [block.text for block in out.content if hasattr(block, "text")]
        assert "init" in texts

    @pytest.mark.asyncio
    async def test_trw_recall_marks_session_active(self, middleware: CeremonyMiddleware) -> None:
        """Without a compaction marker, trw_recall still executes."""
        result = FakeToolResult(content=[TextContent(type="text", text="recall")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-recall")
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=FakeContext(request_context=req_ctx),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert not is_session_active("sess-recall")
        texts = [block.text for block in out.content if hasattr(block, "text")]
        assert "recall" in texts

    @pytest.mark.asyncio
    async def test_heartbeat_exception_inside_safe_does_not_block_tool(
        self,
        middleware: CeremonyMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If touch_heartbeat raises, _touch_heartbeat_safe catches it and the
        tool result is still returned (fail-open).

        Uses a non-trw_* tool to avoid the post-compaction gate
        (trw_* tools are blocked when session is not started).
        """
        monkeypatch.setattr(
            "trw_mcp.state._paths.touch_heartbeat",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
        )

        result = FakeToolResult(content=[TextContent(type="text", text="result")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-hb")
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=FakeContext(request_context=req_ctx),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        texts = [b.text for b in out.content if hasattr(b, "text")]
        assert "result" in texts

    @pytest.mark.asyncio
    async def test_successful_tool_execution_touches_heartbeat(
        self,
        middleware: CeremonyMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        heartbeat_calls: list[str] = []

        monkeypatch.setattr(
            "trw_mcp.state._paths.touch_heartbeat",
            lambda **kwargs: heartbeat_calls.append(str(kwargs["session_id"])),
        )

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="result")])

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id="sess-hb-success"),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        texts = [b.text for b in out.content if hasattr(b, "text")]
        assert "result" in texts
        assert heartbeat_calls == ["sess-hb-success"]

    @pytest.mark.asyncio
    async def test_successful_session_start_touches_heartbeat(
        self,
        middleware: CeremonyMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        heartbeat_calls: list[str] = []

        monkeypatch.setattr(
            "trw_mcp.state._paths.touch_heartbeat",
            lambda **kwargs: heartbeat_calls.append(str(kwargs["session_id"])),
        )

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(
                content=[TextContent(type="text", text='{"success": true}')],
            )

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id="sess-start-hb"),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert is_session_active("sess-start-hb")
        assert out.content[0].text == '{"success": true}'
        assert heartbeat_calls == ["sess-start-hb"]

    @pytest.mark.asyncio
    async def test_unsuccessful_session_start_does_not_activate_session(
        self,
        middleware: CeremonyMiddleware,
    ) -> None:
        """A failed session_start payload leaves the session inactive."""

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(
                content=[TextContent(type="text", text='{"success": false}')],
            )

        req_ctx = FakeRequestContext(session_id="sess-failed-start")
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=FakeContext(request_context=req_ctx),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert not is_session_active("sess-failed-start")
        assert out.content[0].text == '{"success": false}'

    @pytest.mark.asyncio
    async def test_compaction_gate_blocks_trw_tools_until_session_start(
        self,
        middleware: CeremonyMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When recovery is pending, trw_* tools are blocked with a structured error."""
        monkeypatch.setattr(
            "trw_mcp.middleware.ceremony._is_compaction_gate_required",
            lambda: True,
        )

        async def call_next(_ctx: Any) -> Any:
            raise AssertionError("blocked tool should not execute")

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id="sess-gated"),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert out.structured_content["error"] == "session_start_required"
        assert out.structured_content["tool_attempted"] == "trw_status"
        assert "Call trw_session_start()" in out.content[0].text
