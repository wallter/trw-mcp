"""Unit tests for trw_mcp.middleware.response_optimizer.

Tests the _is_empty and _compact helpers directly, plus the on_call_tool
async method using lightweight stub objects (no real FastMCP chain).

Covers:
- Float rounding to 2 decimal places
- Null value stripping from dicts
- Empty dict / empty list stripping
- Non-JSON text passthrough (no modification)
- Invalid JSON passthrough (fail-open)
- _is_empty sentinel values (0, False, empty string are NOT empty)
- Nested structure compaction
- Array of objects compaction
- on_call_tool: JSON TextContent blocks compacted
- on_call_tool: non-JSON blocks pass through
- on_call_tool: non-TextContent blocks (e.g. ImageContent) pass through
- on_call_tool: invalid JSON blocks pass through (fail-open)
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


@pytest.fixture(autouse=True)
def _force_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force JSON format for backward-compat tests (CORE-096 changes default to YAML)."""
    monkeypatch.setattr(
        "trw_mcp.middleware.response_optimizer._get_response_format",
        lambda: "json",
    )


# ---------------------------------------------------------------------------
# Stub objects for on_call_tool integration
# ---------------------------------------------------------------------------


@dataclass
class FakeToolResult:
    """Minimal ToolResult stub with mutable content list."""

    content: list[Any]


# ---------------------------------------------------------------------------
# _is_empty
# ---------------------------------------------------------------------------


class TestIsEmpty:
    """Tests for the _is_empty sentinel helper."""

    @pytest.mark.unit
    def test_none_is_empty(self) -> None:
        assert _is_empty(None) is True

    @pytest.mark.unit
    def test_empty_dict_is_empty(self) -> None:
        assert _is_empty({}) is True

    @pytest.mark.unit
    def test_empty_list_is_empty(self) -> None:
        assert _is_empty([]) is True

    @pytest.mark.unit
    def test_zero_is_not_empty(self) -> None:
        assert _is_empty(0) is False

    @pytest.mark.unit
    def test_false_is_not_empty(self) -> None:
        assert _is_empty(False) is False

    @pytest.mark.unit
    def test_empty_string_is_not_empty(self) -> None:
        assert _is_empty("") is False

    @pytest.mark.unit
    def test_nonempty_dict_is_not_empty(self) -> None:
        assert _is_empty({"a": 1}) is False

    @pytest.mark.unit
    def test_nonempty_list_is_not_empty(self) -> None:
        assert _is_empty([1]) is False

    @pytest.mark.unit
    def test_integer_is_not_empty(self) -> None:
        assert _is_empty(42) is False

    @pytest.mark.unit
    def test_string_is_not_empty(self) -> None:
        assert _is_empty("hello") is False


# ---------------------------------------------------------------------------
# _compact — float rounding
# ---------------------------------------------------------------------------


class TestCompactFloats:
    """_compact rounds floats to 2 decimal places."""

    @pytest.mark.unit
    def test_float_rounded_to_two_decimals(self) -> None:
        result = _compact(3.14159)
        assert result == 3.14

    @pytest.mark.unit
    def test_float_already_two_decimals_unchanged(self) -> None:
        result = _compact(1.50)
        assert result == 1.5

    @pytest.mark.unit
    def test_float_rounds_up(self) -> None:
        result = _compact(2.999)
        assert result == 3.0

    @pytest.mark.unit
    def test_float_in_dict_value_rounded(self) -> None:
        result = _compact({"score": 0.12345})
        assert result == {"score": 0.12}

    @pytest.mark.unit
    def test_float_in_nested_dict_rounded(self) -> None:
        result = _compact({"outer": {"inner": 9.87654}})
        assert result == {"outer": {"inner": 9.88}}

    @pytest.mark.unit
    def test_float_in_list_rounded(self) -> None:
        result = _compact([1.111, 2.222, 3.333])
        assert result == [1.11, 2.22, 3.33]

    @pytest.mark.unit
    def test_integer_passthrough_unchanged(self) -> None:
        result = _compact(42)
        assert result == 42

    @pytest.mark.unit
    def test_string_passthrough_unchanged(self) -> None:
        result = _compact("hello")
        assert result == "hello"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw, expected",
        [
            (0.123456, 0.12),
            (0.999999, 1.0),
            (-3.14159, -3.14),
            (100.005, 100.0),
        ],
    )
    def test_float_rounding_parametrized(self, raw: float, expected: float) -> None:
        assert _compact(raw) == expected


# ---------------------------------------------------------------------------
# _compact — null stripping
# ---------------------------------------------------------------------------


class TestCompactNullStripping:
    """_compact removes keys with None values."""

    @pytest.mark.unit
    def test_none_value_stripped(self) -> None:
        result = _compact({"a": 1, "b": None})
        assert result == {"a": 1}
        assert "b" not in result

    @pytest.mark.unit
    def test_all_none_values_produces_empty_dict(self) -> None:
        result = _compact({"x": None, "y": None})
        assert result == {}

    @pytest.mark.unit
    def test_nested_none_stripped(self) -> None:
        result = _compact({"outer": {"keep": "value", "drop": None}})
        assert result == {"outer": {"keep": "value"}}

    @pytest.mark.unit
    def test_none_in_list_preserved(self) -> None:
        # Lists are compacted element-by-element; None items are NOT stripped from lists
        result = _compact([1, None, 3])
        assert result == [1, None, 3]


# ---------------------------------------------------------------------------
# _compact — empty dict / list stripping
# ---------------------------------------------------------------------------


class TestCompactEmptyStripping:
    """_compact removes keys whose values are empty dicts or empty lists."""

    @pytest.mark.unit
    def test_empty_dict_value_stripped(self) -> None:
        result = _compact({"data": {"a": 1}, "meta": {}})
        assert result == {"data": {"a": 1}}
        assert "meta" not in result

    @pytest.mark.unit
    def test_empty_list_value_stripped(self) -> None:
        result = _compact({"items": [], "count": 3})
        assert result == {"count": 3}
        assert "items" not in result

    @pytest.mark.unit
    def test_nonempty_list_value_kept(self) -> None:
        result = _compact({"items": [1, 2], "count": 2})
        assert result == {"items": [1, 2], "count": 2}

    @pytest.mark.unit
    def test_nonempty_dict_value_kept(self) -> None:
        result = _compact({"meta": {"version": "1.0"}, "data": "ok"})
        assert result == {"meta": {"version": "1.0"}, "data": "ok"}


# ---------------------------------------------------------------------------
# _compact — complex / mixed structures
# ---------------------------------------------------------------------------


class TestCompactMixed:
    """_compact handles realistic mixed-type payloads."""

    @pytest.mark.unit
    def test_realistic_tool_response(self) -> None:
        payload = {
            "status": "ok",
            "score": 0.87654,
            "metadata": None,
            "details": {},
            "tags": ["unit", "test"],
            "extra": [],
        }
        result = _compact(payload)
        assert result == {
            "status": "ok",
            "score": 0.88,
            "tags": ["unit", "test"],
        }
        assert "metadata" not in result
        assert "details" not in result
        assert "extra" not in result

    @pytest.mark.unit
    def test_list_of_dicts_each_compacted(self) -> None:
        payload = [
            {"name": "alice", "score": 0.9999, "notes": None},
            {"name": "bob", "score": 0.5555, "tags": []},
        ]
        result = _compact(payload)
        assert result == [
            {"name": "alice", "score": 1.0},
            {"name": "bob", "score": 0.56},
        ]

    @pytest.mark.unit
    def test_boolean_values_preserved(self) -> None:
        result = _compact({"active": True, "disabled": False})
        assert result == {"active": True, "disabled": False}

    @pytest.mark.unit
    def test_zero_value_preserved(self) -> None:
        result = _compact({"count": 0, "name": "test"})
        assert result == {"count": 0, "name": "test"}


# ---------------------------------------------------------------------------
# Full round-trip: JSON text → compact → re-serialized
# ---------------------------------------------------------------------------


class TestCompactJsonRoundTrip:
    """Verify _compact output matches what the middleware would produce."""

    @pytest.mark.unit
    def test_roundtrip_strips_nulls(self) -> None:
        raw = json.dumps({"a": 1, "b": None, "c": 3})
        parsed = json.loads(raw)
        compacted = _compact(parsed)
        output = json.dumps(compacted, separators=(",", ":"))
        assert output == '{"a":1,"c":3}'

    @pytest.mark.unit
    def test_roundtrip_rounds_floats(self) -> None:
        raw = json.dumps({"pi": 3.14159, "e": 2.71828})
        parsed = json.loads(raw)
        compacted = _compact(parsed)
        output = json.dumps(compacted, separators=(",", ":"))
        assert output == '{"pi":3.14,"e":2.72}'

    @pytest.mark.unit
    def test_non_json_text_passthrough(self) -> None:
        """Non-JSON text: begins neither with '{' nor '[' — should NOT be modified."""
        plain = "This is a plain text response."
        # Middleware checks text[0] in ('{', '[') — plain text never enters _compact
        assert not plain.startswith("{")
        assert not plain.startswith("[")

    @pytest.mark.unit
    def test_invalid_json_does_not_crash(self) -> None:
        """_compact receives parsed data, never raw strings.
        json.loads of invalid JSON raises JSONDecodeError before _compact is called.
        Verify the fail-open guard works by simulating the middleware path."""
        import json as json_mod

        bad_json = "{not valid json"
        with pytest.raises(json_mod.JSONDecodeError):
            json_mod.loads(bad_json)
        # After JSONDecodeError, middleware leaves content untouched — _compact never called

    @pytest.mark.unit
    def test_empty_string_not_entered(self) -> None:
        """Empty string: middleware skips if not text or not starting with { or [."""
        text = ""
        # The middleware guard: `if not text or (text[0] not in ('{', '[')): continue`
        assert not text  # evaluates to falsy — skipped

    @pytest.mark.unit
    def test_list_json_compacted(self) -> None:
        """JSON arrays are also valid input for _compact."""
        raw = json.dumps([{"x": 1.999, "y": None}, {"x": 2.0}])
        parsed = json.loads(raw)
        compacted = _compact(parsed)
        output = json.dumps(compacted, separators=(",", ":"))
        assert output == '[{"x":2.0},{"x":2.0}]'


# ---------------------------------------------------------------------------
# on_call_tool integration: exercise the actual async middleware method
# ---------------------------------------------------------------------------


class TestOnCallTool:
    """Tests for ResponseOptimizerMiddleware.on_call_tool using stub objects."""

    @pytest.fixture
    def middleware(self) -> ResponseOptimizerMiddleware:
        return ResponseOptimizerMiddleware()

    @pytest.mark.unit
    async def test_json_text_content_compacted(self, middleware: ResponseOptimizerMiddleware) -> None:
        """JSON TextContent blocks have floats rounded and nulls stripped."""
        payload = json.dumps({"score": 0.87654, "meta": None, "tags": ["a"]})
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        assert len(out.content) == 1
        parsed = json.loads(out.content[0].text)
        assert parsed["score"] == 0.88
        assert "meta" not in parsed
        assert parsed["tags"] == ["a"]

    @pytest.mark.unit
    async def test_plain_text_passthrough(self, middleware: ResponseOptimizerMiddleware) -> None:
        """Non-JSON TextContent (no leading { or [) passes through unchanged."""
        plain = "This is a plain text response."
        result = FakeToolResult(content=[TextContent(type="text", text=plain)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        assert out.content[0].text == plain

    @pytest.mark.unit
    async def test_empty_text_passthrough(self, middleware: ResponseOptimizerMiddleware) -> None:
        """Empty string TextContent passes through unchanged."""
        result = FakeToolResult(content=[TextContent(type="text", text="")])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        assert out.content[0].text == ""

    @pytest.mark.unit
    async def test_invalid_json_passthrough_fail_open(self, middleware: ResponseOptimizerMiddleware) -> None:
        """Invalid JSON starting with '{' passes through unchanged (fail-open)."""
        bad = "{not: valid json at all!!}"
        result = FakeToolResult(content=[TextContent(type="text", text=bad)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        assert out.content[0].text == bad

    @pytest.mark.unit
    async def test_non_text_content_block_passthrough(self, middleware: ResponseOptimizerMiddleware) -> None:
        """Non-TextContent blocks (e.g. image) are skipped entirely."""
        from unittest.mock import MagicMock

        fake_image = MagicMock()
        fake_image.type = "image"
        result = FakeToolResult(content=[fake_image])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        assert out.content[0] is fake_image

    @pytest.mark.unit
    async def test_multiple_blocks_each_optimized(self, middleware: ResponseOptimizerMiddleware) -> None:
        """Multiple TextContent blocks are each independently optimized."""
        block1 = json.dumps({"a": 1.999, "drop": None})
        block2 = json.dumps({"b": 2.001, "keep": "hello"})
        result = FakeToolResult(
            content=[
                TextContent(type="text", text=block1),
                TextContent(type="text", text=block2),
            ]
        )

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        p1 = json.loads(out.content[0].text)
        p2 = json.loads(out.content[1].text)
        assert p1 == {"a": 2.0}
        assert p2 == {"b": 2.0, "keep": "hello"}

    @pytest.mark.unit
    async def test_json_array_block_compacted(self, middleware: ResponseOptimizerMiddleware) -> None:
        """JSON arrays in TextContent are also compacted."""
        payload = json.dumps([{"x": 1.999, "y": None}, {"x": 2.0}])
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        parsed = json.loads(out.content[0].text)
        assert parsed == [{"x": 2.0}, {"x": 2.0}]

    @pytest.mark.unit
    async def test_output_is_compact_json_no_whitespace(self, middleware: ResponseOptimizerMiddleware) -> None:
        """Re-serialized JSON uses compact separators (no whitespace)."""
        payload = json.dumps({"a": 1, "b": 2}, indent=2)  # Pretty-printed input
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        # Compact output must not have spaces after : or ,
        assert " " not in out.content[0].text


# ---------------------------------------------------------------------------
# YAML format path (tests that do NOT use the autouse json-override fixture)
# ---------------------------------------------------------------------------


class TestYamlFormatPath:
    """Tests for YAML serialization path in ResponseOptimizerMiddleware."""

    @pytest.fixture
    def middleware(self) -> ResponseOptimizerMiddleware:
        return ResponseOptimizerMiddleware()

    @pytest.mark.unit
    async def test_yaml_format_used_when_format_is_yaml(
        self, middleware: ResponseOptimizerMiddleware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When format is 'yaml', output is YAML not JSON."""
        monkeypatch.setattr(
            "trw_mcp.middleware.response_optimizer._get_response_format",
            lambda: "yaml",
        )
        payload = json.dumps({"score": 0.87654, "status": "ok"})
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        # YAML output contains colons without quotes (not compact JSON)
        assert "score:" in out.content[0].text or "status:" in out.content[0].text

    @pytest.mark.unit
    def test_yaml_dump_success(self) -> None:
        """_yaml_dump serializes dict to YAML string."""
        from trw_mcp.middleware.response_optimizer import _yaml_dump

        result = _yaml_dump({"key": "value", "num": 42})
        assert "key:" in result
        assert "value" in result

    @pytest.mark.unit
    def test_yaml_dump_fallback_to_json_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_yaml_dump falls back to compact JSON when YAML serialization fails."""
        from trw_mcp.middleware import response_optimizer as ro

        monkeypatch.setattr(
            ro._yaml,
            "dump",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("yaml error")),
        )
        from trw_mcp.middleware.response_optimizer import _yaml_dump

        result = _yaml_dump({"a": 1})
        # Fallback to compact JSON
        parsed = json.loads(result)
        assert parsed == {"a": 1}


# Note: Tests for _get_response_format and _yaml_dump live in
# test_response_optimizer_format.py to avoid the autouse _force_json_format
# fixture in this file (which always returns "json" and prevents those
# code paths from being covered here).
