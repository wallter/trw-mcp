"""Tests for ContextBudgetMiddleware — observation masking.

Validates turn tracking, verbosity tiers, JSON/text compression,
redundancy detection, config integration, and fail-open behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.types import TextContent

from trw_mcp.middleware._compression import compress_text_block, hash_content
from trw_mcp.middleware.context_budget import (
    ContextBudgetMiddleware,
    get_turn_count,
    get_verbosity_tier,
    reset_state,
)


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    """Reset module-level session state before each test."""
    reset_state()


# --- Helper dataclasses (same pattern as test_middleware_ceremony.py) ---


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


# --- Turn tracking tests ---


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

    def test_reset_state(self) -> None:
        """reset_state clears all counts."""
        from trw_mcp.middleware.context_budget import _turn_counts

        _turn_counts["sess-1"] = 5
        reset_state()
        assert get_turn_count("sess-1") == 0


# --- Verbosity tier tests ---


class TestVerbosityTiers:
    """Tests for get_verbosity_tier with default and custom thresholds."""

    def test_full_tier_default(self) -> None:
        """Turns 1-10 return 'full' with defaults."""
        for turn in range(1, 11):
            assert get_verbosity_tier(turn) == "full"

    def test_compact_tier(self) -> None:
        """Turns 11-30 return 'compact' with defaults."""
        for turn in (11, 20, 30):
            assert get_verbosity_tier(turn) == "compact"

    def test_minimal_tier(self) -> None:
        """Turns 31+ return 'minimal' with defaults."""
        for turn in (31, 50, 100):
            assert get_verbosity_tier(turn) == "minimal"

    def test_custom_thresholds(self) -> None:
        """Custom compact_after and minimal_after values respected."""
        assert get_verbosity_tier(3, compact_after=2, minimal_after=5) == "compact"
        assert get_verbosity_tier(6, compact_after=2, minimal_after=5) == "minimal"
        assert get_verbosity_tier(1, compact_after=2, minimal_after=5) == "full"


# --- JSON compression tests ---


class TestJsonCompression:
    """Tests for JSON TextContent compression at each tier."""

    def test_full_passthrough(self) -> None:
        """JSON content unchanged at full tier."""
        data = json.dumps({"status": "ok", "metadata": {"x": 1}})
        assert compress_text_block(data, "full") == data

    def test_compact_strips_metadata(self) -> None:
        """Keys like 'metadata', 'ceremony' removed at compact tier."""
        data = json.dumps({
            "status": "ok",
            "metadata": {"x": 1},
            "ceremony": {"active": True},
            "debug": "verbose",
            "result": "pass",
        })
        result = json.loads(compress_text_block(data, "compact"))
        assert "status" in result
        assert "result" in result
        assert "metadata" not in result
        assert "ceremony" not in result
        assert "debug" not in result

    def test_compact_truncates_long_strings(self) -> None:
        """Strings >200 chars truncated at compact tier."""
        long_val = "x" * 300
        data = json.dumps({"content": long_val})
        result = json.loads(compress_text_block(data, "compact"))
        assert len(result["content"]) == 201  # 200 + ellipsis char

    def test_minimal_strips_deep_nesting(self) -> None:
        """Objects >2 levels deep replaced at minimal tier."""
        data = json.dumps({
            "level1": {
                "level2": {
                    "level3": {"deep": True},
                },
            },
        })
        result = json.loads(compress_text_block(data, "minimal"))
        assert result["level1"]["level2"] == "[nested]"

    def test_minimal_truncates_short(self) -> None:
        """Strings truncated to 100 chars at minimal tier."""
        val = "y" * 150
        data = json.dumps({"msg": val})
        result = json.loads(compress_text_block(data, "minimal"))
        assert len(result["msg"]) == 101  # 100 + ellipsis char


# --- Non-JSON text compression tests ---


class TestTextCompression:
    """Tests for plain text compression at each tier."""

    def test_text_full_passthrough(self) -> None:
        """Plain text unchanged at full tier."""
        text = "Hello, this is a plain text response."
        assert compress_text_block(text, "full") == text

    def test_text_compact_truncates(self) -> None:
        """Plain text truncated to 500 chars at compact tier."""
        text = "a" * 600
        result = compress_text_block(text, "compact")
        assert result.startswith("a" * 500)
        assert "truncated" in result
        assert "trw_status()" in result

    def test_text_minimal_truncates(self) -> None:
        """Plain text truncated to 200 chars at minimal tier."""
        text = "b" * 400
        result = compress_text_block(text, "minimal")
        assert result.startswith("b" * 200)
        assert "truncated" in result

    def test_short_text_not_truncated(self) -> None:
        """Text within limits is unchanged."""
        text = "short"
        assert compress_text_block(text, "compact") == text
        assert compress_text_block(text, "minimal") == text


# --- Redundancy detection tests ---


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
            return FakeToolResult(
                content=[TextContent(type="text", text=f"output-{call_count}")]
            )

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

        # Different tool with same output — should NOT be detected as redundant
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

        # Same tool+output but different session — should NOT be redundant
        ctx2 = self._make_ctx("trw_status", session_id="sess-2")
        out = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]

        assert "No changes" not in out.content[0].text


# --- Integration and edge case tests ---


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

        req_ctx = FakeRequestContext(session_id="sess-img")
        # Set turn count high to trigger compression
        from trw_mcp.middleware.context_budget import _turn_counts

        _turn_counts["sess-img"] = 20

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(request_context=req_ctx),
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
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: mock_config,
        )

        long_text = "x" * 600

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text=long_text)])

        # Set high turn count to trigger compression (if masking were enabled)
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
        # A JSON payload with a metadata key — compact tier will strip it
        payload = json.dumps({
            "summary": "done",
            "metadata": {"internal": True},
        })
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        # Set turn count to compact tier (>10)
        from trw_mcp.middleware.context_budget import _turn_counts

        session_id = "sess-compress"
        _turn_counts[session_id] = 11  # one below so next call makes it 12

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id=session_id),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        # After compression, metadata key should be stripped
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

        # Turn 11 with default fallback compact_after=10 → compact tier
        payload = json.dumps({"summary": "ok", "metadata": {"foo": "bar"}})

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text=payload)])

        from trw_mcp.middleware.context_budget import _turn_counts

        session_id = "sess-fallback"
        _turn_counts[session_id] = 11  # pre-set so next call makes it 12

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=FakeContext(
                request_context=FakeRequestContext(session_id=session_id),
            ),
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        # With fallback thresholds at turn 12 → compact tier — metadata stripped
        parsed = json.loads(out.content[0].text)
        assert "metadata" not in parsed


# --- Config tests ---


class TestConfig:
    """Tests for TRWConfig observation masking defaults."""

    def test_config_defaults(self) -> None:
        """TRWConfig has correct default values for observation masking."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.observation_masking is True
        assert config.compact_after_turns == 10
        assert config.minimal_after_turns == 30

    def test_config_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variables override defaults."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_OBSERVATION_MASKING", "false")
        monkeypatch.setenv("TRW_COMPACT_AFTER_TURNS", "5")
        monkeypatch.setenv("TRW_MINIMAL_AFTER_TURNS", "15")
        config = TRWConfig()
        assert config.observation_masking is False
        assert config.compact_after_turns == 5
        assert config.minimal_after_turns == 15


# --- Hash utility tests ---


class TestHashContent:
    """Tests for the content hashing utility."""

    def test_same_content_same_hash(self) -> None:
        """Identical TextContent produces identical hashes."""
        c1 = [TextContent(type="text", text="hello")]
        c2 = [TextContent(type="text", text="hello")]
        assert hash_content(c1) == hash_content(c2)

    def test_different_content_different_hash(self) -> None:
        """Different text produces different hashes."""
        c1 = [TextContent(type="text", text="hello")]
        c2 = [TextContent(type="text", text="world")]
        assert hash_content(c1) != hash_content(c2)
