"""Tests for _provenance.py — canonical provenance comment/frontmatter renderer."""

from __future__ import annotations

import re

from trw_mcp.channels._provenance import (
    now_utc_iso8601,
    parse_provenance_comment,
    render_provenance_comment,
    render_provenance_frontmatter,
)


# ---------------------------------------------------------------------------
# now_utc_iso8601
# ---------------------------------------------------------------------------


def test_now_utc_iso8601_format() -> None:
    ts = now_utc_iso8601()
    # Should be YYYY-MM-DDTHH:MM:SS.mmmZ
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts), ts


def test_now_utc_iso8601_ends_with_z() -> None:
    ts = now_utc_iso8601()
    assert ts.endswith("Z")


# ---------------------------------------------------------------------------
# render_provenance_comment — FR19
# ---------------------------------------------------------------------------


def test_render_provenance_comment_multiline_format() -> None:
    result = render_provenance_comment(
        channel_id="cc-01",
        sha="abc123",
        ts="2026-05-28T00:00:00.000Z",
        tier="T2",
        regenerate="trw-mcp channel-render cc-01",
    )
    assert result.startswith("<!-- TRW:PROVENANCE\n")
    assert result.rstrip().endswith("-->")
    assert "generated_by: trw-mcp" in result
    assert "channel_id: cc-01" in result
    assert "sha: abc123" in result
    assert "ts: 2026-05-28T00:00:00.000Z" in result
    assert "tier: T2" in result
    assert "regenerate: trw-mcp channel-render cc-01" in result


def test_render_provenance_comment_trailing_newline() -> None:
    result = render_provenance_comment("x", "sha", "ts", "T0", "cmd")
    assert result.endswith("\n")


def test_render_provenance_comment_all_fields_present() -> None:
    result = render_provenance_comment("ch", "sha1", "ts1", "T1", "regen-cmd")
    required = [
        "<!-- TRW:PROVENANCE",
        "generated_by: trw-mcp",
        "channel_id: ch",
        "sha: sha1",
        "ts: ts1",
        "tier: T1",
        "regenerate: regen-cmd",
        "-->",
    ]
    for field in required:
        assert field in result, f"Missing field: {field!r}"


# ---------------------------------------------------------------------------
# parse_provenance_comment — round-trip
# ---------------------------------------------------------------------------


def test_parse_provenance_comment_round_trip() -> None:
    original = render_provenance_comment(
        channel_id="cc-01",
        sha="deadbeef",
        ts="2026-05-28T12:00:00.000Z",
        tier="T2",
        regenerate="trw-mcp channel-render cc-01",
    )
    parsed = parse_provenance_comment(original)
    assert parsed is not None
    assert parsed["channel_id"] == "cc-01"
    assert parsed["sha"] == "deadbeef"
    assert parsed["ts"] == "2026-05-28T12:00:00.000Z"
    assert parsed["tier"] == "T2"
    assert parsed["generated_by"] == "trw-mcp"


def test_parse_provenance_comment_not_found_returns_none() -> None:
    result = parse_provenance_comment("# No provenance here\nJust some content.\n")
    assert result is None


def test_parse_provenance_comment_embedded_in_larger_document() -> None:
    doc = """\
# CLAUDE.md

Some content.

<!-- TRW:PROVENANCE
generated_by: trw-mcp
channel_id: cc-42
sha: abcd1234
ts: 2026-05-28T00:00:00.000Z
tier: T3
regenerate: trw-mcp channel-render cc-42
-->

More content.
"""
    parsed = parse_provenance_comment(doc)
    assert parsed is not None
    assert parsed["channel_id"] == "cc-42"
    assert parsed["sha"] == "abcd1234"


# ---------------------------------------------------------------------------
# render_provenance_frontmatter
# ---------------------------------------------------------------------------


def test_render_provenance_frontmatter_has_yaml_block() -> None:
    result = render_provenance_frontmatter(
        channel_id="cursor-01",
        sha="cafebabe",
        ts="2026-05-28T00:00:00.000Z",
        tier="T2",
        regenerate="trw-mcp channel-render cursor-01",
        description="Cursor distill rules",
        globs="**/*.py",
        always_apply=True,
    )
    assert result.startswith("---\n")
    assert "description: Cursor distill rules" in result
    assert "globs: **/*.py" in result
    assert "alwaysApply: true" in result
    assert "<!-- TRW:PROVENANCE" in result
    assert "channel_id: cursor-01" in result


def test_render_provenance_frontmatter_always_apply_false() -> None:
    result = render_provenance_frontmatter(
        channel_id="x",
        sha="s",
        ts="t",
        tier="T0",
        regenerate="cmd",
    )
    assert "alwaysApply: false" in result


def test_render_provenance_frontmatter_parseable_provenance() -> None:
    result = render_provenance_frontmatter(
        channel_id="mdc-01",
        sha="ff00ff",
        ts="2026-05-28T00:00:00.000Z",
        tier="T1",
        regenerate="regen",
    )
    parsed = parse_provenance_comment(result)
    assert parsed is not None
    assert parsed["channel_id"] == "mdc-01"
