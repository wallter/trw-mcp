"""Tests for middleware._compression — tier-aware text compression and hashing.

Covers:
- truncate: at/above/below limit
- strip_deep: at max_depth, nested dicts/lists, list items at depth, scalar passthrough at depth
- compress_json: compact vs minimal tier, key stripping
- compress_text_block: JSON vs non-JSON, tier boundaries, invalid JSON fallback
- hash_content: mixed content types
"""

from __future__ import annotations

import json

from mcp.types import TextContent

from trw_mcp.middleware._compression import (
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

    def test_list_items_recursively_stripped(self) -> None:
        """List items at depth < max_depth are recursively processed."""
        # A list at depth 0 with nested dicts — items should be traversed
        data = [{"a": {"b": "deep"}}]
        result = strip_deep(data, max_depth=2)
        # depth 0: list traversed, depth 1: dict traversed, depth 2: "b" replaced
        assert result == [{"a": "[nested]"}]

    def test_list_with_scalars_at_depth_preserved(self) -> None:
        """Scalar items in a list at max_depth are not replaced with [nested]."""
        data = {"items": [1, 2, 3]}
        # At depth=1, items is a list — traversed. At depth=2, each int is a scalar.
        result = strip_deep(data, max_depth=2)
        assert result == {"items": [1, 2, 3]}

    def test_deeply_nested_list_replaced(self) -> None:
        """A list at exactly max_depth is replaced with [nested]."""
        data = {"a": {"b": [1, 2, 3]}}
        # depth 0: dict, depth 1: dict, depth 2: list — list replaced
        result = strip_deep(data, max_depth=2)
        assert result["a"]["b"] == "[nested]"  # type: ignore[index]


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
        assert result["a"]["b"] == "[nested]"  # type: ignore[index]

    def test_recall_payload_drops_context_before_truncating_summaries(self) -> None:
        long_summary = "x" * 250
        data = {
            "learnings": [
                {
                    "id": "L-1",
                    "summary": long_summary,
                    "impact": 0.95,
                    "tags": ["a", "b", "c"],
                    "status": "active",
                }
            ],
            "context": {"architecture": {"big": True}},
        }

        result = compress_json(data, "compact")

        assert "context" not in result
        assert result["learnings"][0] == {  # type: ignore[index]
            "id": "L-1",
            "summary": long_summary,
            "impact": 0.95,
        }

    def test_minimal_recall_payload_keeps_structured_learning_items(self) -> None:
        long_summary = "y" * 350
        data = {
            "auto_recalled": [
                {
                    "id": "L-2",
                    "summary": long_summary,
                    "impact": 0.88,
                    "status": "obsolete",
                }
            ]
        }

        result = compress_json(data, "minimal")

        item = result["auto_recalled"][0]  # type: ignore[index]
        assert item["id"] == "L-2"
        assert len(item["summary"]) == 301
        assert item["summary"].endswith("\u2026")
        assert item["impact"] == 0.88
        assert item["status"] == "obsolete"

    def test_delivery_style_nested_dicts_are_shallow_compacted(self) -> None:
        data = {
            "reflect": {
                "events_analyzed": 41,
                "learnings_produced": 0,
                "status": "success",
                "details": {"large": {"nested": True}},
            }
        }

        result = compress_json(data, "compact")

        assert result["reflect"] == {  # type: ignore[index]
            "events_analyzed": 41,
            "learnings_produced": 0,
            "status": "success",
            "details": "[nested]",
        }


class TestCompressTextBlock:
    def test_full_tier_passthrough(self) -> None:
        text = "x" * 1000
        assert compress_text_block(text, "full") == text

    def test_compact_json_strips_keys(self) -> None:
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

    def test_invalid_json_starting_with_brace_falls_through(self) -> None:
        """Invalid JSON starting with '{' falls through to text truncation."""
        bad_json = "{not: valid: json}" + "x" * 1000
        result = compress_text_block(bad_json, "compact")
        # Should truncate as plain text (not crash)
        assert len(result) < len(bad_json)
        assert "truncated" in result

    def test_invalid_json_array_starting_falls_through(self) -> None:
        """Invalid JSON starting with '[' falls through to text truncation."""
        bad_json = "[not, valid" + "x" * 1000
        result = compress_text_block(bad_json, "minimal")
        assert len(result) < len(bad_json)
        assert "truncated" in result

    def test_empty_text_unchanged(self) -> None:
        assert compress_text_block("", "compact") == ""
        assert compress_text_block("", "minimal") == ""

    def test_recall_json_keeps_long_learning_summary_in_compact_tier(self) -> None:
        summary = "important " * 30
        text = json.dumps(
            {
                "learnings": [
                    {
                        "id": "L-3",
                        "summary": summary,
                        "impact": 0.9,
                        "tags": ["noise"] * 5,
                    }
                ],
                "context": {"conventions": {"verbose": True}},
            }
        )

        result = json.loads(compress_text_block(text, "compact"))

        assert "context" not in result
        assert result["learnings"][0]["summary"] == summary
        assert "tags" not in result["learnings"][0]

    def test_delivery_json_shallow_compacts_nested_status_blocks(self) -> None:
        text = json.dumps(
            {
                "reflect": {
                    "events_analyzed": 41,
                    "learnings_produced": 0,
                    "status": "success",
                    "notes": ["a", "b", "c", "d", "e", "f"],
                    "deep": {"nested": True},
                }
            }
        )

        result = json.loads(compress_text_block(text, "compact"))

        assert result["reflect"]["status"] == "success"
        assert result["reflect"]["notes"] == ["a", "b", "c", "d", "e"]
        assert result["reflect"]["deep"] == "[nested]"


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
