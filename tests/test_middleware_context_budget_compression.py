"""Compression helper tests for context budget middleware."""

from __future__ import annotations

import json

from trw_mcp.middleware._compression import compress_text_block


class TestJsonCompression:
    """Tests for JSON TextContent compression at each tier."""

    def test_full_passthrough(self) -> None:
        """JSON content unchanged at full tier."""
        data = json.dumps({"status": "ok", "metadata": {"x": 1}})
        assert compress_text_block(data, "full") == data

    def test_compact_strips_metadata(self) -> None:
        """Keys like 'metadata', 'ceremony' removed at compact tier."""
        data = json.dumps(
            {
                "status": "ok",
                "metadata": {"x": 1},
                "ceremony": {"active": True},
                "debug": "verbose",
                "result": "pass",
            }
        )
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
        assert len(result["content"]) == 201

    def test_minimal_strips_deep_nesting(self) -> None:
        """Objects >2 levels deep replaced at minimal tier."""
        data = json.dumps(
            {
                "level1": {
                    "level2": {
                        "level3": {"deep": True},
                    },
                },
            }
        )
        result = json.loads(compress_text_block(data, "minimal"))
        assert result["level1"]["level2"] == "[nested]"

    def test_minimal_truncates_short(self) -> None:
        """Strings truncated to 100 chars at minimal tier."""
        val = "y" * 150
        data = json.dumps({"msg": val})
        result = json.loads(compress_text_block(data, "minimal"))
        assert len(result["msg"]) == 101


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
