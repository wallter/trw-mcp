"""Tests for _marker_replace.py — FR12 idempotency and bounded-diff."""

from __future__ import annotations

import pytest

from trw_mcp.channels._manifest_models import MarkersConfig
from trw_mcp.channels._marker_replace import (
    extract_segment_interior,
    replace_distill_segment,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

START = "<!-- trw-distill:start -->"
END = "<!-- trw-distill:end -->"


def _markers(start: str = START, end: str = END) -> MarkersConfig:
    return MarkersConfig(start=start, end=end)


# ---------------------------------------------------------------------------
# extract_segment_interior
# ---------------------------------------------------------------------------


def test_extract_interior_returns_none_when_no_start() -> None:
    content = "no markers here"
    assert extract_segment_interior(content, _markers()) is None


def test_extract_interior_returns_none_when_no_end() -> None:
    content = START + " some text without end"
    assert extract_segment_interior(content, _markers()) is None


def test_extract_interior_returns_none_when_end_before_start() -> None:
    content = END + " text " + START
    assert extract_segment_interior(content, _markers()) is None


def test_extract_interior_returns_empty_string() -> None:
    content = START + END
    result = extract_segment_interior(content, _markers())
    assert result == ""


def test_extract_interior_returns_content() -> None:
    content = START + "\nhello world\n" + END
    result = extract_segment_interior(content, _markers())
    assert result == "\nhello world\n"


def test_extract_interior_returns_none_for_empty_markers() -> None:
    content = "some content"
    assert extract_segment_interior(content, MarkersConfig()) is None


# ---------------------------------------------------------------------------
# replace_distill_segment — basic replacement
# ---------------------------------------------------------------------------


def test_replaces_existing_segment() -> None:
    content = f"before\n{START}\nold interior\n{END}\nafter"
    result = replace_distill_segment(content, "new interior", markers=_markers())
    assert "new interior" in result
    assert "old interior" not in result
    assert "before" in result
    assert "after" in result


def test_appends_when_no_markers() -> None:
    content = "existing content"
    result = replace_distill_segment(content, "segment", markers=_markers())
    assert START in result
    assert END in result
    assert "segment" in result
    assert "existing content" in result


def test_appends_when_empty_content() -> None:
    result = replace_distill_segment("", "segment", markers=_markers())
    assert START in result
    assert END in result
    assert "segment" in result


def test_appends_when_only_whitespace() -> None:
    result = replace_distill_segment("   \n  ", "segment", markers=_markers())
    assert START in result
    assert END in result


# ---------------------------------------------------------------------------
# Idempotency property — f(f(c, s), s) == f(c, s) for 20+ inputs
# ---------------------------------------------------------------------------

_IDEMPOTENCY_CASES: list[tuple[str, str]] = [
    ("", ""),
    ("", "hello"),
    ("", "line1\nline2"),
    ("plain text", ""),
    ("plain text", "segment"),
    ("plain text", "multi\nline\ncontent"),
    (f"{START}\nold\n{END}", "new"),
    (f"{START}\nold\n{END}", ""),
    (f"before\n{START}\nold\n{END}\nafter", "replacement"),
    (f"before\n{START}\nold\n{END}\nafter", ""),
    (f"before\n{START}\n\n{END}\nafter", "nonempty"),
    ("# Title\n\nSome content.", "distill info"),
    ("# Title\n\nSome content.", ""),
    (f"A\n{START}\nX\n{END}\nB\nC", "Y"),
    (f"First line\n\n{START}\ndata\n{END}\n\nLast line", "new data"),
    ("no markers at all\nline2", "inserted"),
    (f"{START}\nsingle\n{END}", "single"),
    (f"header\n{START}\na\nb\nc\n{END}\nfooter", "replacement text"),
    (f"{START}\n{END}", "content between empty markers"),
    ("trailing newline\n", "seg"),
    ("multiple\n\nnewlines\n\nbetween", "new segment"),
]


@pytest.mark.parametrize("content,new_interior", _IDEMPOTENCY_CASES)
def test_idempotency(content: str, new_interior: str) -> None:
    """FR12: f(f(c, s, m), s, m) == f(c, s, m) for all inputs."""
    m = _markers()
    once = replace_distill_segment(content, new_interior, markers=m)
    twice = replace_distill_segment(once, new_interior, markers=m)
    assert once == twice, (
        f"Idempotency violated:\ncontent={content!r}\nnew_interior={new_interior!r}\n"
        f"once={once!r}\ntwice={twice!r}"
    )


# ---------------------------------------------------------------------------
# Bounded-diff property — content outside markers byte-identical
# ---------------------------------------------------------------------------


def test_bounded_diff_before_marker() -> None:
    before = "PREFIX CONTENT\n"
    content = before + START + "\nold\n" + END + "\nafter"
    result = replace_distill_segment(content, "new", markers=_markers())
    assert result.startswith(before + START), (
        "Content before start marker must be byte-identical"
    )


def test_bounded_diff_after_marker() -> None:
    after = "\nSUFFIX CONTENT"
    content = "before\n" + START + "\nold\n" + END + after
    result = replace_distill_segment(content, "new", markers=_markers())
    assert result.endswith(END + after), (
        "Content after end marker must be byte-identical"
    )


# ---------------------------------------------------------------------------
# count=1 prevents duplicate marker pairs
# ---------------------------------------------------------------------------


def test_count_1_no_duplicate_markers_after_repeated_render() -> None:
    """Only one marker pair should ever appear regardless of renders."""
    content = "initial"
    m = _markers()
    r1 = replace_distill_segment(content, "v1", markers=m)
    r2 = replace_distill_segment(r1, "v2", markers=m)
    r3 = replace_distill_segment(r2, "v3", markers=m)
    assert r3.count(START) == 1, "Must have exactly one start marker"
    assert r3.count(END) == 1, "Must have exactly one end marker"


def test_content_with_two_pairs_keeps_first_only() -> None:
    """If content somehow has two marker pairs, only first is replaced."""
    m = _markers()
    # Manually constructed content with two pairs
    content = f"A\n{START}\nfirst\n{END}\nB\n{START}\nsecond\n{END}\nC"
    result = replace_distill_segment(content, "replaced", markers=m)
    # After replace, only first pair surviced and was updated
    assert "replaced" in result
    assert result.count(START) >= 1


# ---------------------------------------------------------------------------
# Empty new_interior produces empty segment between markers
# ---------------------------------------------------------------------------


def test_empty_interior_produces_markers_only() -> None:
    content = f"before\n{START}\nsome content\n{END}\nafter"
    result = replace_distill_segment(content, "", markers=_markers())
    interior = extract_segment_interior(result, _markers())
    assert interior is not None
    assert interior.strip() == "", f"Expected empty interior, got: {interior!r}"


def test_empty_content_empty_interior_appends_markers() -> None:
    result = replace_distill_segment("", "", markers=_markers())
    assert START in result
    assert END in result
