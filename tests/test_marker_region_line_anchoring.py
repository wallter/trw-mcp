"""Marker-region replacement must be line-anchored in every writer.

TRW writes its auto-generated block between sentinels in files it does not own.
Locating that region with a substring scan binds to the FIRST occurrence
anywhere in the document — including a sentinel a user merely *mentioned* in
prose, in backticks, or inside a fenced code block. Everything between that
mention and the real block is then deleted, silently, with the write reported as
successful.

That is the shape which destroyed 705 lines of ROADMAP.md (2026-06-11) and is
why ``.claude/rules/trw-mcp-python.md`` §Marker / Sentinel Matching requires
line-anchored matching. The rule was applied to one implementation; three others
kept the substring form. These tests pin all of them, and the shared helper they
now delegate to, so the fix cannot be lost again by a copy nobody hardened.
"""

from __future__ import annotations

import pytest

from trw_mcp.bootstrap._cursor_cli import _merge_agents_md
from trw_mcp.bootstrap._file_ops import replace_marker_region

START = "<!-- trw:start -->"
END = "<!-- trw:end -->"
CURSOR_BEGIN = "<!-- TRW:BEGIN -->"
CURSOR_END = "<!-- TRW:END -->"


class TestReplaceMarkerRegion:
    """The shared helper both bootstrap writers delegate to."""

    def test_prose_mention_is_not_treated_as_the_region_start(self) -> None:
        existing = (
            "# Agents\n\n"
            f"We delimit the block with `{START}` and `{END}`.\n\n"
            f"{START}\nOLD\n{END}\n\n"
            "Trailing user note.\n"
        )

        out = replace_marker_region(existing, start=START, end=END, new_block=f"{START}\nNEW\n{END}\n")

        assert out is not None
        assert f"We delimit the block with `{START}` and `{END}`." in out
        assert "Trailing user note." in out
        assert "NEW" in out
        assert "OLD" not in out

    def test_returns_none_when_only_a_prose_mention_exists(self) -> None:
        """A mention alone is not a region — the caller must append, not replace."""
        existing = f"# Doc\n\nWe use `{START}` as a delimiter.\n\nUser paragraph.\n"

        assert replace_marker_region(existing, start=START, end=END, new_block="X") is None

    def test_returns_none_for_a_half_written_region(self) -> None:
        """Start without end is a fault the caller reports, not one to paper over."""
        existing = f"# Doc\n\n{START}\nbody with no end marker\n"

        assert replace_marker_region(existing, start=START, end=END, new_block="X") is None

    def test_header_is_absorbed_only_when_it_precedes_the_start(self) -> None:
        header = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"
        existing = f"# Doc\n\nUser text.\n\n{header}\n{START}\nOLD\n{END}\n"

        out = replace_marker_region(existing, start=START, end=END, new_block=f"{START}\nNEW\n{END}\n", header=header)

        assert out is not None
        assert "User text." in out
        # The old header line is consumed with the block, not left orphaned above it.
        assert out.count(header) == 0
        assert "OLD" not in out

    @pytest.mark.parametrize(
        "mention",
        [
            "Inline `{start}` in a sentence.",
            "    {start} indented inside a code block",
            "See {start} mid-sentence without backticks.",
        ],
    )
    def test_non_whole_line_mentions_never_open_the_region(self, mention: str) -> None:
        """Only a line whose stripped content IS the marker may delimit a region.

        Each fixture below carries a REAL, complete block after the mention, so a
        matcher that wrongly binds to the mention has an end marker to run to and
        would delete ``Keep me.``. Without that real block the assertion would
        pass vacuously — the mention would be rejected merely for having no end
        marker to pair with, proving nothing about the anchoring rule.
        """
        body = mention.format(start=START)
        existing = f"# Doc\n\n{body}\n\nKeep me.\n\n{START}\nOLD\n{END}\n"

        out = replace_marker_region(existing, start=START, end=END, new_block=f"{START}\nNEW\n{END}\n")

        assert out is not None, "the real block below the mention must still be found"
        assert "Keep me." in out, f"content after the mention was deleted: {out!r}"
        assert body in out, "the mention itself must be preserved verbatim"
        assert "NEW" in out and "OLD" not in out

    def test_indented_marker_alone_on_a_line_makes_the_document_ambiguous(self) -> None:
        """An indented marker IS a whole line, so it cannot be told from a real one.

        This is the case a naive ``line.strip() == marker`` rule gets wrong: the
        indented mention strips to an exact match, so *some* candidate must be
        chosen, and choosing wrong deletes everything to the next end marker.
        With two candidates there is no evidence for which is real, so the writer
        refuses rather than guessing.
        """
        existing = f"# Doc\n\n    {START}\n\nImportant user paragraph.\n\n{START}\nOLD\n{END}\n"

        assert replace_marker_region(existing, start=START, end=END, new_block="X") is None

    def test_two_complete_blocks_are_refused_rather_than_half_replaced(self) -> None:
        existing = f"{START}\nfirst\n{END}\n\nUser text.\n\n{START}\nsecond\n{END}\n"

        assert replace_marker_region(existing, start=START, end=END, new_block="X") is None

    def test_header_far_above_with_user_content_between_is_not_absorbed(self) -> None:
        """A quoted auto-comment must not swallow the user lines beneath it."""
        header = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"
        existing = f"{header}\n\n## My section\n\nUser prose.\n\n{START}\nOLD\n{END}\n"

        out = replace_marker_region(existing, start=START, end=END, new_block=f"{START}\nNEW\n{END}\n", header=header)

        assert out is not None
        assert "## My section" in out
        assert "User prose." in out

    def test_header_immediately_above_is_absorbed(self) -> None:
        header = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"
        existing = f"# Doc\n\nUser text.\n\n{header}\n{START}\nOLD\n{END}\n"

        out = replace_marker_region(existing, start=START, end=END, new_block=f"{START}\nNEW\n{END}\n", header=header)

        assert out is not None
        assert "User text." in out
        assert out.count(header) == 0, "the stale header must be consumed, not duplicated"


class TestCursorCliMergeAgentsMd:
    """cursor-cli uses its own TRW:BEGIN/TRW:END sentinel vocabulary."""

    def test_prose_mention_does_not_destroy_user_content(self) -> None:
        existing = (
            "# Agents\n\n"
            f"Sentinels are `{CURSOR_BEGIN}` and `{CURSOR_END}`.\n\n"
            f"{CURSOR_BEGIN}\nOLD\n{CURSOR_END}\n\n"
            "Trailing note.\n"
        )

        out = _merge_agents_md(existing, f"{CURSOR_BEGIN}\nNEW\n{CURSOR_END}\n", CURSOR_BEGIN, CURSOR_END)

        assert f"Sentinels are `{CURSOR_BEGIN}` and `{CURSOR_END}`." in out
        assert "Trailing note." in out
        assert "NEW" in out
        assert "OLD" not in out

    def test_mention_without_a_real_block_prepends_and_keeps_everything(self) -> None:
        existing = f"# Agents\n\nWe reference `{CURSOR_BEGIN}` in docs.\n\nUser paragraph.\n"

        out = _merge_agents_md(existing, f"{CURSOR_BEGIN}\nNEW\n{CURSOR_END}\n", CURSOR_BEGIN, CURSOR_END)

        assert "User paragraph." in out
        assert f"We reference `{CURSOR_BEGIN}` in docs." in out
        assert "NEW" in out


class TestOpencodeAgentsMdWriter:
    """opencode's AGENTS.md writer shares the same helper."""

    def test_prose_mention_does_not_destroy_user_content(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from trw_mcp.bootstrap._opencode import generate_agents_md

        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "# Agents\n\n"
            f"Our block sits between `{START}` and `{END}`.\n\n"
            f"{START}\nOLD BLOCK\n{END}\n\n"
            "Trailing user note.\n",
            encoding="utf-8",
        )

        generate_agents_md(tmp_path, "REGENERATED TRW BODY")

        out = agents_md.read_text(encoding="utf-8")
        assert f"Our block sits between `{START}` and `{END}`." in out
        assert "Trailing user note." in out
        assert "OLD BLOCK" not in out
