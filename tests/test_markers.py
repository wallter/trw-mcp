"""Tests for inline comment marker regex — extract_marker_ids."""

from __future__ import annotations

from trw_mcp.state.anchor_generation import MARKER_PATTERN, extract_marker_ids


class TestExtractMarkerIds:
    def test_single_id(self) -> None:
        ids = extract_marker_ids("# mcp.trw.recall(id=L-a3Fq)")
        assert ids == ["L-a3Fq"]

    def test_multiple_ids(self) -> None:
        ids = extract_marker_ids("# mcp.trw.recall(id=L-a3Fq,L-b2Xp)")
        assert ids == ["L-a3Fq", "L-b2Xp"]

    def test_go_comment(self) -> None:
        ids = extract_marker_ids("// mcp.trw.recall(id=L-a3Fq)")
        assert ids == ["L-a3Fq"]

    def test_rust_comment(self) -> None:
        ids = extract_marker_ids("// mcp.trw.recall(id=L-xY9z)")
        assert ids == ["L-xY9z"]

    def test_block_comment(self) -> None:
        ids = extract_marker_ids("/* mcp.trw.recall(id=L-a3Fq) */")
        assert ids == ["L-a3Fq"]

    def test_sql_comment(self) -> None:
        ids = extract_marker_ids("-- mcp.trw.recall(id=L-a3Fq)")
        assert ids == ["L-a3Fq"]

    def test_space_separated_no_match(self) -> None:
        # Space-separated IDs don't match — only comma-separated is supported.
        # The closing ')' must immediately follow the last ID; a space before ')' breaks the match.
        ids = extract_marker_ids("# mcp.trw.recall(id=L-a3Fq L-b2Xp)")
        assert ids == []

    def test_no_markers(self) -> None:
        ids = extract_marker_ids("def my_function():\n    pass\n")
        assert ids == []

    def test_old_hex_id_match(self) -> None:
        # Old 8-char hex IDs should match (4-8 chars supported)
        ids = extract_marker_ids("# mcp.trw.recall(id=L-a1b2c3d4)")
        assert ids == ["L-a1b2c3d4"]

    def test_three_ids(self) -> None:
        ids = extract_marker_ids("# mcp.trw.recall(id=L-a3Fq,L-b2Xp,L-c1Yz)")
        assert ids == ["L-a3Fq", "L-b2Xp", "L-c1Yz"]

    def test_deduplication(self) -> None:
        # Same ID appearing in two markers in the same text
        text = "# mcp.trw.recall(id=L-a3Fq)\n# mcp.trw.recall(id=L-a3Fq)"
        ids = extract_marker_ids(text)
        assert ids == ["L-a3Fq"]

    def test_multiline_code(self) -> None:
        code = (
            "def foo():\n"
            "    # mcp.trw.recall(id=L-a3Fq)\n"
            "    pass\n"
            "\n"
            "def bar():\n"
            "    # mcp.trw.recall(id=L-b2Xp)\n"
            "    pass\n"
        )
        ids = extract_marker_ids(code)
        assert "L-a3Fq" in ids
        assert "L-b2Xp" in ids

    def test_marker_pattern_exported(self) -> None:
        # MARKER_PATTERN is exported for external use
        assert MARKER_PATTERN is not None
        import re

        assert isinstance(MARKER_PATTERN, re.Pattern)

    def test_four_char_id(self) -> None:
        # Minimum length (4 chars)
        ids = extract_marker_ids("# mcp.trw.recall(id=L-abcd)")
        assert ids == ["L-abcd"]

    def test_eight_char_id(self) -> None:
        # Maximum length (8 chars)
        ids = extract_marker_ids("# mcp.trw.recall(id=L-abcdefgh)")
        assert ids == ["L-abcdefgh"]

    def test_too_short_id_no_match(self) -> None:
        # Less than 4 chars after hyphen should not match
        ids = extract_marker_ids("# mcp.trw.recall(id=L-ab)")
        assert ids == []

    def test_too_long_id_no_match(self) -> None:
        # More than 8 chars after hyphen should not match
        ids = extract_marker_ids("# mcp.trw.recall(id=L-abcdefghi)")
        assert ids == []

    def test_uppercase_prefix(self) -> None:
        # Any letter prefix works (L, R, etc.)
        ids = extract_marker_ids("# mcp.trw.recall(id=R-a3Fq)")
        assert ids == ["R-a3Fq"]

    def test_mixed_case_id(self) -> None:
        ids = extract_marker_ids("# mcp.trw.recall(id=L-aAbB)")
        assert ids == ["L-aAbB"]

    def test_empty_string(self) -> None:
        ids = extract_marker_ids("")
        assert ids == []
