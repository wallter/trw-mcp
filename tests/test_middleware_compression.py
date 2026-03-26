"""Tests for middleware._compression — tier-aware text compression and hashing.

Covers:
- truncate: at/above/below limit
- strip_deep: at max_depth, nested dicts/lists
- compress_json: compact vs minimal tier, key stripping
- compress_text_block: JSON vs non-JSON, tier boundaries
- hash_content: mixed content types
"""

from __future__ import annotations

from mcp.types import TextContent

from trw_mcp.middleware._compression import (
    STRIP_KEYS,
    compress_json,
    compress_text_block,
    hash_content,
    strip_deep,
    truncate,
)


class TestTruncate:
    def test_short_string_unchanged(self) -> None:
        assert truncate("hello", 10) == "hello"

    def test_at_limit_unchanged(self) -> None:
        assert truncate("12345", 5) == "12345"

    def test_above_limit_truncated(self) -> None:
        result = truncate("123456", 5)
        assert len(result) == 6  # 5 chars + ellipsis
        assert result.endswith("\u2026")

    def test_empty_string(self) -> None:
        assert truncate("", 10) == ""


class TestStripDeep:
    def test_flat_dict_preserved(self) -> None:
        data = {"a": 1, "b": 2}
        assert strip_deep(data, max_depth=2) == {"a": 1, "b": 2}

    def test_nested_at_max_depth_replaced(self) -> None:
        data = {"a": {"b": {"c": "deep"}}}
        result = strip_deep(data, max_depth=2)
        assert result == {"a": {"b": "[nested]"}}

    def test_list_at_max_depth_replaced(self) -> None:
        data = {"a": {"b": [1, 2, 3]}}
        result = strip_deep(data, max_depth=2)
        assert result == {"a": {"b": "[nested]"}}

    def test_scalar_at_max_depth_preserved(self) -> None:
        data = {"a": {"b": "hello"}}
        result = strip_deep(data, max_depth=2)
        assert result == {"a": {"b": "hello"}}

    def test_zero_depth_replaces_all_containers(self) -> None:
        assert strip_deep({"a": 1}, max_depth=0) == "[nested]"
        assert strip_deep([1, 2], max_depth=0) == "[nested]"
        assert strip_deep("scalar", max_depth=0) == "scalar"


class TestCompressJson:
    def test_strip_keys_removed_at_compact(self) -> None:
        data = {"summary": "ok", "metadata": {"foo": "bar"}, "debug": True}
        result = compress_json(data, "compact")
        assert "summary" in result
        assert "metadata" not in result
        assert "debug" not in result

    def test_strings_truncated_at_minimal(self) -> None:
        long_str = "x" * 200
        data = {"summary": long_str}
        result = compress_json(data, "minimal")
        assert len(result["summary"]) <= 101  # 100 + ellipsis

    def test_non_dict_passthrough(self) -> None:
        assert compress_json([1, 2, 3], "compact") == [1, 2, 3]
        assert compress_json("string", "minimal") == "string"

    def test_minimal_strips_deep_nesting(self) -> None:
        data = {"a": {"b": {"c": {"d": "deep"}}}}
        result = compress_json(data, "minimal")
        assert result["a"]["b"] == "[nested]"


class TestCompressTextBlock:
    def test_full_tier_passthrough(self) -> None:
        text = "x" * 1000
        assert compress_text_block(text, "full") == text

    def test_compact_json_strips_keys(self) -> None:
        import json
        data = {"summary": "ok", "metadata": {"foo": "bar"}}
        text = json.dumps(data)
        result = compress_text_block(text, "compact")
        parsed = json.loads(result)
        assert "metadata" not in parsed

    def test_non_json_truncated_at_minimal(self) -> None:
        long_text = "x" * 500
        result = compress_text_block(long_text, "minimal")
        assert len(result) < 500
        assert "truncated" in result

    def test_non_json_truncated_at_compact(self) -> None:
        long_text = "x" * 1000
        result = compress_text_block(long_text, "compact")
        assert len(result) < 1000
        assert "truncated" in result


class TestHashContent:
    def test_same_content_same_hash(self) -> None:
        content = [TextContent(type="text", text="hello")]
        assert hash_content(content) == hash_content(content)

    def test_different_content_different_hash(self) -> None:
        a = [TextContent(type="text", text="hello")]
        b = [TextContent(type="text", text="world")]
        assert hash_content(a) != hash_content(b)

    def test_empty_content(self) -> None:
        result = hash_content([])
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_mixed_content_types(self) -> None:
        """Non-TextContent blocks should be ignored in hash."""
        text_only = [TextContent(type="text", text="hello")]
        mixed = [TextContent(type="text", text="hello"), object()]  # type: ignore[list-item]
        assert hash_content(text_only) == hash_content(mixed)
