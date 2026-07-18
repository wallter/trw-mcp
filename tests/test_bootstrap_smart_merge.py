"""Tests for shared marker-based smart-merge utilities.

The per-client instruction generators historically had identical smart-merge
logic with copy-pasted differences; that logic was consolidated into the
``_file_ops.py`` helpers ``smart_merge_marker_section`` and
``write_instruction_file_with_merge`` (covered here). The Copilot generator
exercises the shared path via the cross-client parity check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._file_ops import (
    smart_merge_marker_section,
    write_instruction_file_with_merge,
)

_START = "<!-- trw:test:start -->"
_END = "<!-- trw:test:end -->"


def _section(body: str) -> str:
    return f"{_START}\n{body}\n{_END}"


# ── smart_merge_marker_section ────────────────────────────────────────────


class TestSmartMergeMarkerSection:
    def test_empty_existing_returns_section_with_newline(self) -> None:
        out = smart_merge_marker_section("", _section("hello"), start_marker=_START, end_marker=_END)
        assert out == _section("hello") + "\n"

    def test_replaces_between_markers_preserving_user_content(self) -> None:
        existing = "# My GEMINI.md\n\nuser preamble\n\n" + _section("OLD trw body") + "\n\nuser postamble\n"
        new = _section("NEW trw body")

        out = smart_merge_marker_section(existing, new, start_marker=_START, end_marker=_END)

        assert "user preamble" in out
        assert "user postamble" in out
        assert "OLD trw body" not in out
        assert "NEW trw body" in out

    def test_idempotent_when_section_already_present(self) -> None:
        section = _section("identical body")
        existing = "before\n\n" + section + "\n\nafter\n"

        once = smart_merge_marker_section(existing, section, start_marker=_START, end_marker=_END)
        twice = smart_merge_marker_section(once, section, start_marker=_START, end_marker=_END)

        assert once == existing
        assert twice == once

    def test_appends_when_no_markers(self) -> None:
        existing = "user-authored prose\nwith multiple lines\n"
        section = _section("trw body")

        out = smart_merge_marker_section(existing, section, start_marker=_START, end_marker=_END)

        # User content preserved verbatim at the head, TRW section appended.
        assert out.startswith("user-authored prose\nwith multiple lines")
        assert out.rstrip("\n").endswith(_END)

    def test_appends_when_only_start_marker_present(self) -> None:
        existing = f"{_START}\nuser opened the marker by accident\n"
        section = _section("trw body")

        out = smart_merge_marker_section(existing, section, start_marker=_START, end_marker=_END)

        # Treated as no valid pair → append.
        assert out.endswith(section + "\n")

    def test_appends_when_end_before_start_corrupted(self) -> None:
        existing = f"{_END}\nmangled\n{_START}\n"
        section = _section("trw body")

        out = smart_merge_marker_section(existing, section, start_marker=_START, end_marker=_END)

        assert out.endswith(section + "\n")

    def test_empty_existing_with_strip(self) -> None:
        out = smart_merge_marker_section("   \n\n", _section("body"), start_marker=_START, end_marker=_END)
        # No leading separator because existing strips to empty.
        assert out.startswith(_START)

    def test_inline_prose_mention_not_treated_as_section_start(self) -> None:
        """An inline prose mention of the start marker must NOT open the section.

        Marker matching is line-anchored; a marker referenced mid-paragraph
        (e.g. inside backticks) is not a whole-line delimiter. With no real
        markers present the section is appended and the inline mention survives
        verbatim — the substring form would have spliced from the mention and
        destroyed surrounding user prose (the 705-line ROADMAP incident).
        """
        existing = "intro line\nuse `<!-- trw:test:start -->` to open the block\nmore prose\n"
        section = _section("trw body")

        out = smart_merge_marker_section(existing, section, start_marker=_START, end_marker=_END)

        assert "use `<!-- trw:test:start -->` to open the block" in out
        assert "more prose" in out
        assert out.rstrip("\n").endswith(_END)
        assert out.count("trw body") == 1

    def test_inline_mention_above_real_markers_replaces_only_whole_line_section(self) -> None:
        """A whole-line section is replaced even when an inline mention precedes it.

        The line-anchored search must skip the earlier backticked mention and
        bind to the real whole-line start marker, so only the genuine section is
        swapped and the user's prose (including the inline mention) is preserved.
        """
        existing = "prose with `<!-- trw:test:start -->` inline\n\n" + _section("OLD body") + "\n\ntail\n"

        out = smart_merge_marker_section(existing, _section("NEW body"), start_marker=_START, end_marker=_END)

        assert "prose with `<!-- trw:test:start -->` inline" in out
        assert "OLD body" not in out
        assert "NEW body" in out
        assert "tail" in out


# ── write_instruction_file_with_merge ────────────────────────────────────


class TestWriteInstructionFileWithMerge:
    def test_creates_when_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=_section("body"),
            start_marker=_START,
            end_marker=_END,
            force=False,
            result=result,
        )

        assert target.exists()
        assert "FOO.md" in result["created"]
        assert result["preserved"] == []
        assert result["errors"] == []

    def test_preserves_when_already_identical(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        section = _section("body")
        target.write_text(section + "\n", encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=section,
            start_marker=_START,
            end_marker=_END,
            force=False,
            result=result,
        )

        assert "FOO.md" in result["preserved"]
        assert result["created"] == []
        assert result["updated"] == []

    def test_updates_when_section_changed(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        target.write_text("user preamble\n\n" + _section("OLD") + "\n", encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=_section("NEW"),
            start_marker=_START,
            end_marker=_END,
            force=False,
            result=result,
        )

        assert "FOO.md" in result["updated"]
        text = target.read_text(encoding="utf-8")
        assert "user preamble" in text
        assert "NEW" in text and "OLD" not in text

    def test_force_rewrites_even_with_user_content(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        target.write_text("user-authored content with no markers\n", encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=_section("trw"),
            start_marker=_START,
            end_marker=_END,
            force=True,
            result=result,
        )

        # force=True overwrites entirely — user content is lost (documented behavior).
        text = target.read_text(encoding="utf-8")
        assert text == _section("trw")
        assert "FOO.md" in result["updated"]

    def test_idempotent_after_initial_create(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        section = _section("body")

        for run_idx in range(3):
            result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
            write_instruction_file_with_merge(
                target_path=target,
                rel_path="FOO.md",
                trw_section=section,
                start_marker=_START,
                end_marker=_END,
                force=False,
                result=result,
            )
            if run_idx == 0:
                assert "FOO.md" in result["created"]
            else:
                assert "FOO.md" in result["preserved"]


# ── Cross-client parity check ────────────────────────────────────────────


class TestClientParityGoldenPath:
    """The Copilot instruction generator follows the shared-helper contract."""

    @pytest.mark.parametrize(
        "module_path,filename,marker_start",
        [
            (
                "trw_mcp.bootstrap._copilot",
                ".github/copilot-instructions.md",
                "<!-- trw:copilot:start -->",
            ),
        ],
    )
    def test_smart_merge_preserves_pre_existing_user_content(
        self, tmp_path: Path, module_path: str, filename: str, marker_start: str
    ) -> None:
        import importlib

        module = importlib.import_module(module_path)
        gen = module.generate_copilot_instructions

        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# user-authored\nimportant project rules\n",
            encoding="utf-8",
        )

        result = gen(tmp_path)

        text = target.read_text(encoding="utf-8")
        assert "user-authored" in text, "user content preserved"
        assert "important project rules" in text
        assert marker_start in text, "TRW section appended"
        assert result["errors"] == []
