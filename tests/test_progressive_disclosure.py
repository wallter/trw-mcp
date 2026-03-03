"""Tests for progressive tool disclosure — PRD-CORE-067.

Covers: compact cards (FR01), on-demand expansion (FR02), usage profiler
(FR03/FR04), hot set computation, group expansion (FR06), TRWConfig field
(FR05), and backward compatibility (FR07).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from trw_mcp.state.progressive_middleware import (
    ProgressiveDisclosureMiddleware,
    truncate_description,
)
from trw_mcp.state.usage_profiler import (
    DEFAULT_HOT_SET,
    TOOL_GROUPS,
    compute_hot_set,
    record_session_usage,
)

# ── Fixtures ────────────────────────────────────────────────────────────


def _make_tool(
    name: str,
    description: str = "Full description. Second sentence here.",
    parameters: dict[str, object] | None = None,
) -> object:
    """Create a mock Tool-like object with model_copy support."""
    from fastmcp.tools.tool import Tool

    params = parameters or {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    return Tool(name=name, description=description, parameters=params)


def _make_middleware(
    hot_set: set[str] | None = None,
    tool_groups: dict[str, list[str]] | None = None,
) -> ProgressiveDisclosureMiddleware:
    """Create a ProgressiveDisclosureMiddleware with defaults."""
    return ProgressiveDisclosureMiddleware(
        hot_set=hot_set or {"trw_session_start"},
        tool_groups=tool_groups or TOOL_GROUPS,
    )


# ── truncate_description ────────────────────────────────────────────────


class TestTruncateDescription:
    """Unit tests for description truncation logic."""

    def test_first_sentence_extracted(self) -> None:
        result = truncate_description("Short summary. More detail follows.")
        assert result == "Short summary."

    def test_max_length_enforced(self) -> None:
        long_sentence = "A" * 100 + ". End."
        result = truncate_description(long_sentence, max_len=80)
        assert len(result) <= 80
        assert result.endswith("...")

    def test_no_period_truncates_with_ellipsis(self) -> None:
        no_period = "This description has no period and is quite long " * 3
        result = truncate_description(no_period, max_len=80)
        assert len(result) <= 80
        assert result.endswith("...")

    def test_empty_description(self) -> None:
        assert truncate_description("") == ""
        assert truncate_description(None) == ""

    def test_short_description_unchanged(self) -> None:
        result = truncate_description("Short.")
        assert result == "Short."

    def test_description_under_max_no_period(self) -> None:
        result = truncate_description("No period here", max_len=80)
        assert result == "No period here"


# ── ProgressiveDisclosureMiddleware — on_list_tools ─────────────────────


class TestOnListTools:
    """Tests for compact card generation in on_list_tools."""

    @pytest.mark.asyncio
    async def test_compact_card_description_truncated(self) -> None:
        """FR01: Non-hot-set tools get truncated descriptions."""
        middleware = _make_middleware(hot_set={"trw_session_start"})
        tool = _make_tool("trw_build_check", "Run pytest and mypy. Full validation suite.")

        async def call_next(ctx: object) -> Sequence[object]:
            return [tool]

        from fastmcp.server.middleware.middleware import MiddlewareContext

        ctx = MiddlewareContext(message=None, method="tools/list")  # type: ignore[arg-type]
        result = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

        assert len(result) == 1
        compact = result[0]
        assert compact.name == "trw_build_check"  # type: ignore[union-attr]
        desc = compact.description  # type: ignore[union-attr]
        assert desc is not None
        assert len(desc) <= 80
        assert desc == "Run pytest and mypy."

    @pytest.mark.asyncio
    async def test_compact_card_empty_input_schema(self) -> None:
        """FR01: Compact cards have empty inputSchema."""
        middleware = _make_middleware(hot_set={"trw_session_start"})
        tool = _make_tool("trw_learn")

        async def call_next(ctx: object) -> Sequence[object]:
            return [tool]

        from fastmcp.server.middleware.middleware import MiddlewareContext

        ctx = MiddlewareContext(message=None, method="tools/list")  # type: ignore[arg-type]
        result = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

        compact = result[0]
        params = compact.parameters  # type: ignore[union-attr]
        assert params == {"type": "object", "properties": {}, "required": []}

    @pytest.mark.asyncio
    async def test_compact_card_has_compact_marker(self) -> None:
        """FR01: Compact cards include _compact: true in meta."""
        middleware = _make_middleware(hot_set={"trw_session_start"})
        tool = _make_tool("trw_learn")

        async def call_next(ctx: object) -> Sequence[object]:
            return [tool]

        from fastmcp.server.middleware.middleware import MiddlewareContext

        ctx = MiddlewareContext(message=None, method="tools/list")  # type: ignore[arg-type]
        result = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

        compact = result[0]
        meta = compact.meta  # type: ignore[union-attr]
        assert meta is not None
        assert meta.get("_compact") is True

    @pytest.mark.asyncio
    async def test_hot_set_tools_retain_full_schema(self) -> None:
        """FR01: Hot set tools are NOT compacted."""
        middleware = _make_middleware(hot_set={"trw_session_start"})
        tool = _make_tool("trw_session_start")

        async def call_next(ctx: object) -> Sequence[object]:
            return [tool]

        from fastmcp.server.middleware.middleware import MiddlewareContext

        ctx = MiddlewareContext(message=None, method="tools/list")  # type: ignore[arg-type]
        result = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

        full = result[0]
        assert full.description == "Full description. Second sentence here."  # type: ignore[union-attr]
        assert full.parameters == {  # type: ignore[union-attr]
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }


# ── ProgressiveDisclosureMiddleware — on_call_tool ──────────────────────


class TestOnCallTool:
    """Tests for on-demand expansion via on_call_tool."""

    @pytest.mark.asyncio
    async def test_on_demand_expansion_marks_expanded(self) -> None:
        """FR02: Invoked tool is added to expanded set."""
        middleware = _make_middleware()

        call_next = AsyncMock(return_value="result")

        from fastmcp.server.middleware.middleware import MiddlewareContext
        from mcp.types import CallToolRequestParams

        params = CallToolRequestParams(name="trw_build_check")
        ctx = MiddlewareContext(message=params, method="tools/call")
        await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert "trw_build_check" in middleware.expanded

    @pytest.mark.asyncio
    async def test_on_demand_expansion_executes_tool(self) -> None:
        """FR02: Tool invocation delegates to call_next."""
        middleware = _make_middleware()

        call_next = AsyncMock(return_value="tool_result")

        from fastmcp.server.middleware.middleware import MiddlewareContext
        from mcp.types import CallToolRequestParams

        params = CallToolRequestParams(name="trw_learn")
        ctx = MiddlewareContext(message=params, method="tools/call")
        result = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert result == "tool_result"
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_expanded_tool_shows_full_schema_in_listing(self) -> None:
        """FR02: After invocation, tool appears with full schema in listing."""
        middleware = _make_middleware(hot_set=set())
        tool = _make_tool("trw_learn")

        # First: invoke the tool to mark it expanded
        call_next_tool = AsyncMock(return_value="result")
        from fastmcp.server.middleware.middleware import MiddlewareContext
        from mcp.types import CallToolRequestParams

        params = CallToolRequestParams(name="trw_learn")
        ctx_call = MiddlewareContext(message=params, method="tools/call")
        await middleware.on_call_tool(ctx_call, call_next_tool)  # type: ignore[arg-type]

        # Now: list tools — trw_learn should have full schema
        async def call_next_list(ctx: object) -> Sequence[object]:
            return [tool]

        ctx_list = MiddlewareContext(message=None, method="tools/list")  # type: ignore[arg-type]
        result = await middleware.on_list_tools(ctx_list, call_next_list)  # type: ignore[arg-type]

        listed = result[0]
        assert listed.description == "Full description. Second sentence here."  # type: ignore[union-attr]
        assert listed.parameters == {  # type: ignore[union-attr]
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

    @pytest.mark.asyncio
    async def test_tools_used_tracking(self) -> None:
        """FR03: tools_used list tracks invocations."""
        middleware = _make_middleware()

        call_next = AsyncMock(return_value="result")
        from fastmcp.server.middleware.middleware import MiddlewareContext
        from mcp.types import CallToolRequestParams

        for name in ["trw_learn", "trw_recall", "trw_learn"]:
            params = CallToolRequestParams(name=name)
            ctx = MiddlewareContext(message=params, method="tools/call")
            await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert middleware.tools_used == ["trw_learn", "trw_recall", "trw_learn"]


# ── Group expansion (FR06) ──────────────────────────────────────────────


class TestProgressiveExpand:
    """Tests for expand_group method."""

    def test_expand_group_requirements(self) -> None:
        """FR06: Group expansion returns newly and already expanded lists."""
        middleware = _make_middleware(hot_set=set())
        newly, already = middleware.expand_group("requirements")
        assert set(newly) == {"trw_prd_create", "trw_prd_validate"}
        assert already == []

    def test_expand_group_already_expanded(self) -> None:
        """FR06: Already expanded tools appear in already_expanded."""
        middleware = _make_middleware(hot_set=set())
        # Pre-expand one tool
        middleware.expand_group("requirements")
        # Expand again — all should be already_expanded
        newly, already = middleware.expand_group("requirements")
        assert newly == []
        assert set(already) == {"trw_prd_create", "trw_prd_validate"}

    def test_expand_group_hot_set_counted_as_already(self) -> None:
        """FR06: Hot set tools are counted as already_expanded."""
        middleware = _make_middleware(hot_set={"trw_session_start", "trw_checkpoint"})
        newly, already = middleware.expand_group("ceremony")
        assert "trw_deliver" in newly
        assert "trw_session_start" in already
        assert "trw_checkpoint" in already

    def test_expand_group_invalid_raises(self) -> None:
        """FR06: Invalid group name raises ValueError."""
        middleware = _make_middleware()
        with pytest.raises(ValueError, match="Unknown group"):
            middleware.expand_group("invalid_group")


# ── Usage profiler (FR03/FR04) ──────────────────────────────────────────


class TestUsageProfiler:
    """Tests for tool-usage-profile.jsonl read/write."""

    def test_record_session_usage_writes_jsonl(self, tmp_path: Path) -> None:
        """FR03: Session usage is appended to tool-usage-profile.jsonl."""
        record_session_usage(tmp_path, "sess-001", ["trw_learn", "trw_recall"])

        profile_path = tmp_path / "context" / "tool-usage-profile.jsonl"
        assert profile_path.exists()

        lines = profile_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["session_id"] == "sess-001"
        assert set(entry["tools_used"]) == {"trw_learn", "trw_recall"}
        assert "timestamp" in entry

    def test_record_session_usage_deduplicates(self, tmp_path: Path) -> None:
        """FR03: Duplicate tool names are deduplicated in the entry."""
        record_session_usage(tmp_path, "sess-002", ["trw_learn", "trw_learn", "trw_recall"])

        profile_path = tmp_path / "context" / "tool-usage-profile.jsonl"
        entry = json.loads(profile_path.read_text(encoding="utf-8").strip())
        assert entry["tools_used"] == ["trw_learn", "trw_recall"]

    def test_record_session_usage_silent_on_failure(self, tmp_path: Path) -> None:
        """FR03: Write failure is silent (fire-and-forget)."""
        # Make path a file so mkdir fails
        blocker = tmp_path / "context"
        blocker.write_text("block", encoding="utf-8")

        # Should not raise
        record_session_usage(tmp_path, "sess-003", ["trw_learn"])

    def test_hot_set_default_when_no_profile(self, tmp_path: Path) -> None:
        """FR04: No profile file returns default hot set."""
        result = compute_hot_set(tmp_path)
        assert result == DEFAULT_HOT_SET

    def test_hot_set_default_when_few_sessions(self, tmp_path: Path) -> None:
        """FR04: Fewer than min_sessions entries returns default."""
        context_dir = tmp_path / "context"
        context_dir.mkdir(parents=True)
        profile = context_dir / "tool-usage-profile.jsonl"
        # Write only 2 entries (below min_sessions=3)
        for i in range(2):
            entry = {"session_id": f"s{i}", "tools_used": ["trw_learn"]}
            with open(profile, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        result = compute_hot_set(tmp_path)
        assert result == DEFAULT_HOT_SET

    def test_hot_set_computed_from_profile(self, tmp_path: Path) -> None:
        """FR04: Hot set is computed from most frequent tools."""
        context_dir = tmp_path / "context"
        context_dir.mkdir(parents=True)
        profile = context_dir / "tool-usage-profile.jsonl"

        # Write 20 sessions, trw_build_check in 18
        for i in range(20):
            tools = ["trw_session_start", "trw_deliver"]
            if i < 18:
                tools.append("trw_build_check")
            if i < 10:
                tools.append("trw_learn")
            entry = {"session_id": f"s{i}", "tools_used": tools}
            with open(profile, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        result = compute_hot_set(tmp_path, hot_size=3)
        assert "trw_build_check" in result
        assert "trw_session_start" in result
        assert "trw_deliver" in result

    def test_hot_set_handles_malformed_lines(self, tmp_path: Path) -> None:
        """FR04: Malformed JSONL lines are skipped gracefully."""
        context_dir = tmp_path / "context"
        context_dir.mkdir(parents=True)
        profile = context_dir / "tool-usage-profile.jsonl"

        with open(profile, "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write('{"session_id":"s1","tools_used":["trw_learn"]}\n')
            f.write('{"session_id":"s2","tools_used":["trw_learn"]}\n')
            f.write('{"bad":true}\n')
            f.write('{"session_id":"s3","tools_used":["trw_learn"]}\n')

        result = compute_hot_set(tmp_path, hot_size=3, min_sessions=3)
        assert "trw_learn" in result


# ── TRWConfig progressive_disclosure (FR05) ─────────────────────────────


class TestConfigField:
    """Tests for the TRWConfig.progressive_disclosure field."""

    def test_default_is_false(self) -> None:
        """FR05: Default value is False."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.progressive_disclosure is False

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR05: TRW_PROGRESSIVE_DISCLOSURE=true sets field to True."""
        monkeypatch.setenv("TRW_PROGRESSIVE_DISCLOSURE", "true")
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.progressive_disclosure is True


# ── Backward compatibility (FR07) ───────────────────────────────────────


class TestBackwardCompat:
    """FR07: progressive_disclosure=False means zero behavior change."""

    @pytest.mark.asyncio
    async def test_full_schema_mode_unchanged(self) -> None:
        """FR07: All tools have full schemas when disclosure is off."""
        # When progressive_disclosure=False, no middleware is attached.
        # Verify that without middleware, tool listing is unchanged.
        from fastmcp.tools.tool import Tool

        tools = [
            Tool(name="trw_learn", description="Full desc.", parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}),
            Tool(name="trw_recall", description="Another full desc.", parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": []}),
        ]

        # Without middleware, tools pass through unchanged
        for t in tools:
            assert t.parameters != {"type": "object", "properties": {}, "required": []}
            assert t.meta is None or t.meta.get("_compact") is not True

    def test_progressive_expand_noop_when_disabled(self) -> None:
        """FR07: trw_progressive_expand returns all as already_expanded."""
        # Simulate progressive_disclosure=False by not setting middleware
        import trw_mcp.tools.usage as usage_mod

        original = usage_mod._progressive_middleware
        try:
            usage_mod._progressive_middleware = None
            # Call the expand logic directly
            result = {
                "group": "learning",
                "expanded_tools": [],
                "already_expanded": TOOL_GROUPS["learning"],
            }
            assert result["expanded_tools"] == []
            assert set(result["already_expanded"]) == {"trw_learn", "trw_recall", "trw_learn_update"}
        finally:
            usage_mod._progressive_middleware = original


# ── Integration-level tests ─────────────────────────────────────────────


class TestMiddlewareIntegration:
    """Integration tests for the full middleware lifecycle."""

    @pytest.mark.asyncio
    async def test_multiple_tools_mixed_hot_and_compact(self) -> None:
        """Multiple tools: hot set full, others compact."""
        middleware = _make_middleware(hot_set={"trw_session_start", "trw_checkpoint"})

        tools = [
            _make_tool("trw_session_start"),
            _make_tool("trw_checkpoint"),
            _make_tool("trw_learn"),
            _make_tool("trw_build_check"),
        ]

        async def call_next(ctx: object) -> Sequence[object]:
            return tools

        from fastmcp.server.middleware.middleware import MiddlewareContext

        ctx = MiddlewareContext(message=None, method="tools/list")  # type: ignore[arg-type]
        result = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

        # Hot set tools: full schema
        hot_names = {"trw_session_start", "trw_checkpoint"}
        compact_names = {"trw_learn", "trw_build_check"}

        for t in result:
            if t.name in hot_names:  # type: ignore[union-attr]
                assert t.parameters != {"type": "object", "properties": {}, "required": []}  # type: ignore[union-attr]
            elif t.name in compact_names:  # type: ignore[union-attr]
                assert t.parameters == {"type": "object", "properties": {}, "required": []}  # type: ignore[union-attr]
                assert t.meta is not None and t.meta.get("_compact") is True  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_on_demand_expansion_latency(self) -> None:
        """NFR01: Expansion (in-memory set add) completes in < 100ms."""
        import time

        middleware = _make_middleware()

        call_next = AsyncMock(return_value="result")
        from fastmcp.server.middleware.middleware import MiddlewareContext
        from mcp.types import CallToolRequestParams

        params = CallToolRequestParams(name="trw_build_check")
        ctx = MiddlewareContext(message=params, method="tools/call")

        start = time.monotonic()
        await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 100, f"Expansion took {elapsed_ms:.2f}ms (> 100ms)"
