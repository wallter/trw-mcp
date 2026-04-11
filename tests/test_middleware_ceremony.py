"""Tests for CeremonyMiddleware — server-side ceremony enforcement.

PRD-INFRA-007: Validates per-session state tracking, warning prepending,
exempt tools, graceful fallback, and multi-session isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.ceremony import (
    CEREMONY_TOOLS,
    CEREMONY_WARNING,
    CeremonyMiddleware,
    _compaction_gate_sessions,
    _extract_session_start_payload,
    _session_start_succeeded,
    _known_sessions,
    is_session_active,
    mark_session_active,
    reset_state,
)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset module-level session state before each test."""
    reset_state()


# --- Helper dataclasses to simulate FastMCP middleware types ---


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
    timestamp: datetime = datetime.now(timezone.utc)


@dataclass
class FakeToolResult:
    """Minimal ToolResult stub with mutable content list."""

    content: list[Any]


# --- Unit tests for state functions ---


class TestSessionState:
    """Tests for module-level session state management."""

    @pytest.mark.unit
    def test_new_session_is_not_active(self) -> None:
        assert not is_session_active("new-session")

    @pytest.mark.unit
    def test_mark_session_active(self) -> None:
        mark_session_active("sess-1")
        assert is_session_active("sess-1")

    @pytest.mark.unit
    def test_different_sessions_independent(self) -> None:
        mark_session_active("sess-1")
        assert is_session_active("sess-1")
        assert not is_session_active("sess-2")

    @pytest.mark.unit
    def test_reset_clears_all(self) -> None:
        mark_session_active("sess-1")
        mark_session_active("sess-2")
        reset_state()
        assert not is_session_active("sess-1")
        assert not is_session_active("sess-2")

    @pytest.mark.unit
    def test_mark_idempotent(self) -> None:
        mark_session_active("sess-1")
        mark_session_active("sess-1")
        assert is_session_active("sess-1")

    @pytest.mark.unit
    def test_reset_then_mark_works(self) -> None:
        """After reset, marking a new session should work normally."""
        mark_session_active("sess-before")
        reset_state()
        mark_session_active("sess-after")
        assert is_session_active("sess-after")
        assert not is_session_active("sess-before")

    @pytest.mark.unit
    def test_reset_idempotent(self) -> None:
        """Calling reset_state twice should not raise."""
        reset_state()
        reset_state()
        assert not is_session_active("any-session")


# --- Tests for exempt tools constant ---


class TestCeremonyTools:
    """Tests for the CEREMONY_TOOLS constant."""

    @pytest.mark.unit
    def test_contains_session_start(self) -> None:
        assert "trw_session_start" in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_contains_init(self) -> None:
        assert "trw_init" not in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_contains_recall(self) -> None:
        assert "trw_recall" not in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_is_frozenset(self) -> None:
        assert isinstance(CEREMONY_TOOLS, frozenset)

    @pytest.mark.unit
    def test_nonempty(self) -> None:
        assert len(CEREMONY_TOOLS) > 0

    @pytest.mark.unit
    def test_non_ceremony_tools_excluded(self) -> None:
        """Delivery and checkpoint tools are NOT ceremony initializers."""
        assert "trw_deliver" not in CEREMONY_TOOLS
        assert "trw_checkpoint" not in CEREMONY_TOOLS

    @pytest.mark.unit
    def test_immutable(self) -> None:
        """frozenset is immutable — add() must raise."""
        with pytest.raises((AttributeError, TypeError)):
            CEREMONY_TOOLS.add("trw_fake_tool")  # type: ignore[attr-defined]


# --- Tests for CeremonyMiddleware ---


class TestCeremonyMiddleware:
    """Tests for the CeremonyMiddleware on_call_tool behavior."""

    @pytest.fixture
    def middleware(self) -> CeremonyMiddleware:
        return CeremonyMiddleware()

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
        """Calling a ceremony tool marks the session as active."""
        result = FakeToolResult(content=[TextContent(type="text", text="started")])

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
        """Non-exempt tool called AFTER ceremony has no warning."""
        start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
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

        # First: ceremony tool
        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=fake_ctx,
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        # Second: non-ceremony tool — should have NO warning
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

        # Session 1: run ceremony
        req_ctx1 = FakeRequestContext(session_id="sess-1")
        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=FakeContext(request_context=req_ctx1),
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        # Session 2: no ceremony — non-trw tool gets warning prepended
        req_ctx2 = FakeRequestContext(session_id="sess-2")
        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=FakeContext(request_context=req_ctx2),
        )
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 2  # warning + result

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
        # Patch at the source so the except block in _touch_heartbeat_safe fires
        monkeypatch.setattr(
            "trw_mcp.state._paths.touch_heartbeat",
            lambda: (_ for _ in ()).throw(RuntimeError("disk full")),
        )

        result = FakeToolResult(content=[TextContent(type="text", text="result")])

        async def call_next(_ctx: Any) -> Any:
            return result

        req_ctx = FakeRequestContext(session_id="sess-hb")
        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=FakeContext(request_context=req_ctx),
        )
        # Fail-open: exception inside _touch_heartbeat_safe must not propagate
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        # Result still delivered despite heartbeat failure
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
            lambda: heartbeat_calls.append("touched"),
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
        assert heartbeat_calls == ["touched"]

    @pytest.mark.asyncio
    async def test_successful_session_start_touches_heartbeat(
        self,
        middleware: CeremonyMiddleware,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        heartbeat_calls: list[str] = []

        monkeypatch.setattr(
            "trw_mcp.state._paths.touch_heartbeat",
            lambda: heartbeat_calls.append("touched"),
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
        assert heartbeat_calls == ["touched"]

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


class TestSessionStartPayloadHelpers:
    """Tests for session_start payload extraction and success detection helpers."""

    def test_extracts_structured_content_dict(self) -> None:
        result = type("Result", (), {"structured_content": {"success": True}})()
        assert _extract_session_start_payload(result) == {"success": True}

    def test_extracts_json_payload_from_text_block(self) -> None:
        result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success"}')])
        assert _extract_session_start_payload(result) == {"status": "success"}

    def test_non_list_content_returns_none(self) -> None:
        result = type("Result", (), {"content": "not-a-list"})()
        assert _extract_session_start_payload(result) is None

    def test_session_start_succeeded_handles_status_strings(self) -> None:
        success_result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success"}')])
        failure_result = FakeToolResult(content=[TextContent(type="text", text='{"status":"failed"}')])

        assert _session_start_succeeded(success_result) is True
        assert _session_start_succeeded(failure_result) is False

    def test_compaction_gate_marks_all_known_sessions_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "trw_mcp.middleware.ceremony._is_compaction_gate_required",
            lambda: True,
        )
        _known_sessions.update({"sess-a", "sess-b"})

        from trw_mcp.middleware.ceremony import _is_compaction_gate_required_for_session

        assert _is_compaction_gate_required_for_session("sess-a") is True
        assert _compaction_gate_sessions["sess-b"] is True
        assert _is_compaction_gate_required_for_session("sess-b") is True


class TestCeremonyWarningText:
    """Tests for the warning text content — value-oriented framing."""

    @pytest.mark.unit
    def test_warning_is_nonempty_string(self) -> None:
        assert isinstance(CEREMONY_WARNING, str)
        assert len(CEREMONY_WARNING.strip()) > 0

    @pytest.mark.unit
    def test_warning_mentions_session_start(self) -> None:
        assert "trw_session_start()" in CEREMONY_WARNING

    @pytest.mark.unit
    def test_warning_uses_value_framing(self) -> None:
        """Warning explains what the agent gains, not what it loses."""
        lower = CEREMONY_WARNING.lower()
        assert "learnings" in lower
        assert "run state" in lower

    @pytest.mark.unit
    def test_warning_avoids_threat_framing(self) -> None:
        """No CRITICAL/MUST/WILL threat language."""
        assert "CRITICAL" not in CEREMONY_WARNING
        assert "ACTION REQUIRED" not in CEREMONY_WARNING
        assert "WILL repeat" not in CEREMONY_WARNING
