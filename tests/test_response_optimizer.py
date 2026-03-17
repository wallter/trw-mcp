"""Tests for ResponseOptimizerMiddleware — compact JSON responses.

Validates float rounding, null/empty stripping, and end-to-end middleware
integration for optimizing MCP tool responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.response_optimizer import (
    ResponseOptimizerMiddleware,
    _compact,
    _is_empty,
)


# --- Unit tests for _is_empty ---


@pytest.mark.unit
def test_is_empty_none() -> None:
    assert _is_empty(None) is True


@pytest.mark.unit
def test_is_empty_empty_dict() -> None:
    assert _is_empty({}) is True


@pytest.mark.unit
def test_is_empty_empty_list() -> None:
    assert _is_empty([]) is True


@pytest.mark.unit
def test_is_empty_zero_is_not_empty() -> None:
    assert _is_empty(0) is False


@pytest.mark.unit
def test_is_empty_false_is_not_empty() -> None:
    assert _is_empty(False) is False


@pytest.mark.unit
def test_is_empty_zero_float_is_not_empty() -> None:
    assert _is_empty(0.0) is False


@pytest.mark.unit
def test_is_empty_empty_string_is_not_empty() -> None:
    assert _is_empty("") is False


@pytest.mark.unit
def test_is_empty_nonempty_dict_is_not_empty() -> None:
    assert _is_empty({"a": 1}) is False


@pytest.mark.unit
def test_is_empty_nonempty_list_is_not_empty() -> None:
    assert _is_empty([1]) is False


# --- Unit tests for _compact ---


@pytest.mark.unit
def test_compact_rounds_floats() -> None:
    """Nested dict with floats at various depths gets rounded to 2dp."""
    data = {
        "score": 0.123456,
        "nested": {
            "value": 3.14159265,
            "deep": {"ratio": 0.999999},
        },
        "items": [1.111111, 2.222222],
    }
    result = _compact(data)
    assert result == {
        "score": 0.12,
        "nested": {
            "value": 3.14,
            "deep": {"ratio": 1.0},
        },
        "items": [1.11, 2.22],
    }


@pytest.mark.unit
def test_compact_strips_none() -> None:
    """Keys with None values are removed."""
    data = {"keep": "yes", "remove": None, "also_keep": 42}
    result = _compact(data)
    assert result == {"keep": "yes", "also_keep": 42}


@pytest.mark.unit
def test_compact_strips_empty_collections() -> None:
    """Keys with empty dicts and empty lists are removed."""
    data = {"keep": "yes", "empty_dict": {}, "empty_list": [], "also_keep": 1}
    result = _compact(data)
    assert result == {"keep": "yes", "also_keep": 1}


@pytest.mark.unit
def test_compact_preserves_zero_and_false() -> None:
    """0, 0.0, False, and empty string are NOT stripped."""
    data = {
        "zero_int": 0,
        "zero_float": 0.0,
        "false_bool": False,
        "empty_str": "",
    }
    result = _compact(data)
    assert result == {
        "zero_int": 0,
        "zero_float": 0.0,
        "false_bool": False,
        "empty_str": "",
    }


@pytest.mark.unit
def test_compact_preserves_strings_and_ints() -> None:
    """Non-float primitives pass through unchanged."""
    data = {"name": "test", "count": 42, "flag": True}
    result = _compact(data)
    assert result == {"name": "test", "count": 42, "flag": True}


@pytest.mark.unit
def test_compact_handles_nested_none_and_empty() -> None:
    """Stripping works recursively in nested structures."""
    data = {
        "outer": {
            "keep": 1,
            "drop_none": None,
            "drop_empty": {},
        },
        "list_of_dicts": [
            {"a": 1, "b": None},
            {"c": [], "d": "keep"},
        ],
    }
    result = _compact(data)
    assert result == {
        "outer": {"keep": 1},
        "list_of_dicts": [
            {"a": 1},
            {"d": "keep"},
        ],
    }


@pytest.mark.unit
def test_compact_top_level_list() -> None:
    """Top-level list is handled correctly."""
    data = [1.556, None, {"a": None, "b": 2.999}]
    result = _compact(data)
    assert result == [1.56, None, {"b": 3.0}]


@pytest.mark.unit
def test_compact_none_in_list_preserved() -> None:
    """None values inside lists are preserved (only dict keys with None are stripped)."""
    data = [None, 1, None]
    result = _compact(data)
    assert result == [None, 1, None]


# --- Middleware integration tests ---


@dataclass
class FakeToolResult:
    """Minimal ToolResult stub with mutable content list."""

    content: list[Any]


@dataclass
class FakeMessage:
    """Minimal CallToolRequestParams stub."""

    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class FakeMiddlewareContext:
    """Minimal MiddlewareContext stub."""

    message: FakeMessage
    fastmcp_context: Any = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_middleware_optimizes_json_content() -> None:
    """Full middleware integration: JSON TextContent is compacted."""
    original_json = json.dumps({"score": 0.123456, "meta": None, "tags": []})
    result = FakeToolResult(
        content=[TextContent(type="text", text=original_json)]
    )

    middleware = ResponseOptimizerMiddleware()

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_status"))
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert len(out.content) == 1
    parsed = json.loads(out.content[0].text)
    assert parsed == {"score": 0.12}
    # Verify compact separators (no spaces)
    assert out.content[0].text == '{"score":0.12}'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_middleware_ignores_non_json_text() -> None:
    """Plain text content passes through untouched."""
    plain_text = "This is not JSON at all."
    result = FakeToolResult(
        content=[TextContent(type="text", text=plain_text)]
    )

    middleware = ResponseOptimizerMiddleware()

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_status"))
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert len(out.content) == 1
    assert out.content[0].text == plain_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_middleware_handles_json_array() -> None:
    """JSON array content is also optimized."""
    original_json = json.dumps([{"val": 1.23456}, {"val": 2.34567}])
    result = FakeToolResult(
        content=[TextContent(type="text", text=original_json)]
    )

    middleware = ResponseOptimizerMiddleware()

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_status"))
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    parsed = json.loads(out.content[0].text)
    assert parsed == [{"val": 1.23}, {"val": 2.35}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_middleware_multiple_content_blocks() -> None:
    """Middleware processes each TextContent block independently."""
    result = FakeToolResult(
        content=[
            TextContent(type="text", text='{"a": 1.556}'),
            TextContent(type="text", text="plain text"),
            TextContent(type="text", text='{"b": null, "c": 3}'),
        ]
    )

    middleware = ResponseOptimizerMiddleware()

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_status"))
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert len(out.content) == 3
    assert out.content[0].text == '{"a":1.56}'
    assert out.content[1].text == "plain text"
    assert out.content[2].text == '{"c":3}'


@pytest.mark.unit
@pytest.mark.asyncio
async def test_middleware_malformed_json_passthrough() -> None:
    """JSON that starts with { but is invalid passes through untouched."""
    malformed = '{not valid json at all'
    result = FakeToolResult(
        content=[TextContent(type="text", text=malformed)]
    )

    middleware = ResponseOptimizerMiddleware()

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_status"))
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert out.content[0].text == malformed
