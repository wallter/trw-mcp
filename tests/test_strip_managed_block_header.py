"""Uninstall removes the TRW auto-generated header comment with the block it heads.

The header ``TRW_AUTO_COMMENT`` is written ABOVE ``<!-- trw:start -->``. Row
UNINSTALL-AGENTS-HEADER-LEFTOVER: it used to survive uninstall. Tier A
(deletes bytes in a user file): only an EXACT header line directly above a
removed TRW span goes; every other byte is preserved.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes

START = "<!-- trw:start -->"
END = "<!-- trw:end -->"
MARKERS = ((START, END),)


def _header() -> str:
    from trw_mcp.state.claude_md import TRW_AUTO_COMMENT

    return TRW_AUTO_COMMENT


def _strip(text: str) -> tuple[str, bool, list[str]]:
    from trw_mcp.bootstrap._user_file_edit import strip_managed_block

    return strip_managed_block(text, MARKERS)


@pytest.mark.unit
class TestStripManagedBlockHeader:
    def test_header_directly_above_span_is_removed(self) -> None:
        out, changed, _w = _strip(f"user above\n{_header()}\n{START}\nmanaged\n{END}\nuser below\n")
        assert changed is True
        assert out == "user above\nuser below\n"

    def test_header_with_blank_lines_before_marker_is_removed_with_them(self) -> None:
        out, _c, _w = _strip(f"user above\n\n{_header()}\n\n\n{START}\nmanaged\n{END}\n\nuser below\n")
        # The blank line before the header and the one after the end marker are the user's.
        assert out == "user above\n\n\nuser below\n"

    @pytest.mark.parametrize(
        "near_miss",
        [
            "<!-- TRW AUTO-GENERATED — do not edit between markers --> extra",
            "<!-- TRW AUTO-GENERATED — do not edit between markers -->  ",
            "  <!-- TRW AUTO-GENERATED — do not edit between markers -->",
            "<!-- TRW AUTO-GENERATED — do not edit -->",
            "<!-- TRW AUTO-GENERATED — do not edit between markers --> and more words",
            "<!-- trw auto-generated — do not edit between markers -->",
        ],
    )
    def test_near_miss_header_is_kept(self, near_miss: str) -> None:
        text = f"user above\n{near_miss}\n{START}\nmanaged\n{END}\nuser below\n"
        out, changed, _w = _strip(text)
        assert changed is True
        assert out == f"user above\n{near_miss}\nuser below\n"

    def test_header_separated_from_span_by_user_text_is_kept(self) -> None:
        text = f"{_header()}\nuser text between\n{START}\nmanaged\n{END}\nuser below\n"
        out, _c, _w = _strip(text)
        assert out == f"{_header()}\nuser text between\nuser below\n"

    def test_header_below_span_is_kept(self) -> None:
        text = f"{START}\nmanaged\n{END}\n{_header()}\nuser below\n"
        out, _c, _w = _strip(text)
        assert out == f"{_header()}\nuser below\n"

    def test_header_above_orphan_start_is_kept(self) -> None:
        text = f"user\n{_header()}\n{START}\nno end here\n"
        out, changed, warnings = _strip(text)
        assert changed is False
        assert out == text
        assert warnings

    def test_second_unrelated_header_elsewhere_is_kept(self) -> None:
        text = f"{_header()}\nold user note\n\n{_header()}\n{START}\nmanaged\n{END}\n"
        out, _c, _w = _strip(text)
        assert out == f"{_header()}\nold user note\n\n"

    def test_no_span_file_is_untouched(self) -> None:
        text = f"user\n{_header()}\nmore user\n"
        out, changed, warnings = _strip(text)
        assert changed is False
        assert out == text
        assert warnings == []

    def test_crlf_line_endings_preserved(self) -> None:
        text = f"user above\r\n{_header()}\r\n\r\n{START}\r\nmanaged\r\n{END}\r\nuser below\r\n"
        out, changed, _w = _strip(text)
        assert changed is True
        assert out == "user above\r\nuser below\r\n"

    def test_crlf_near_miss_kept_byte_exact(self) -> None:
        text = f"a\r\n{_header()} x\r\n{START}\r\nm\r\n{END}\r\nb"
        out, _c, _w = _strip(text)
        assert out == f"a\r\n{_header()} x\r\nb"

    def test_header_at_file_start_no_trailing_newline(self) -> None:
        out, _c, _w = _strip(f"{_header()}\n{START}\nmanaged\n{END}")
        assert out == ""


@pytest.mark.integration
class TestUninstallRemovesHeader:
    @pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md"])
    def test_remove_managed_block_file_drops_header_keeps_user_bytes(self, tmp_path: Path, name: str) -> None:
        from trw_mcp.server._subcommands_uninstall_config import _remove_managed_block_file

        above = "# My project\r\n\r\nnotes above\r\n" if name == "CLAUDE.md" else "# My project\n\nnotes above\n"
        eol = "\r\n" if name == "CLAUDE.md" else "\n"
        below = f"{eol}notes below{eol}last line no eol"
        block = f"{_header()}{eol}{START}{eol}managed{eol}{END}{eol}"
        target = tmp_path / name
        target.write_bytes((above + block + below).encode())
        sibling = tmp_path / "keep.txt"
        sibling.write_text("untouched")
        before = snapshot_user_bytes(tmp_path)

        status = _remove_managed_block_file(target, tmp_path, dry_run=False)

        assert status == "stripped"
        assert target.read_bytes() == (above + below).encode()
        assert _header().encode() not in target.read_bytes()
        assert sibling.read_text() == "untouched"
        assert_user_bytes_preserved({d: n for d, n in before.items() if "keep.txt" in n}, tmp_path)

    def test_header_only_agents_md_after_strip_is_kept_empty_not_left_as_header(self, tmp_path: Path) -> None:
        """CLAUDE-MD S2: a project's AGENTS.md is never deleted; emptied, it stays, without TRW's header."""
        from trw_mcp.server._subcommands_uninstall_config import _remove_managed_block_file

        target = tmp_path / "AGENTS.md"
        target.write_text(f"{_header()}\n{START}\nmanaged\n{END}\n")
        assert _remove_managed_block_file(target, tmp_path, dry_run=False) == "stripped"
        assert target.read_text() == ""

    def test_dry_run_does_not_modify(self, tmp_path: Path) -> None:
        from trw_mcp.server._subcommands_uninstall_config import _remove_managed_block_file

        raw = f"user\n{_header()}\n{START}\nm\n{END}\nafter\n"
        target = tmp_path / "AGENTS.md"
        target.write_text(raw)
        assert _remove_managed_block_file(target, tmp_path, dry_run=True) == "stripped"
        assert target.read_text() == raw

    def test_real_writer_install_then_uninstall_leaves_no_header(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import init_project
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        (tmp_path / ".git").mkdir()
        agents = tmp_path / "AGENTS.md"
        user_prefix = "MY OWN HEADER\nsome user text\nTRAILING USER LINE\n"
        agents.write_text(user_prefix)
        result = init_project(tmp_path, ide="all")
        assert not result["errors"], result["errors"]
        assert _header() in agents.read_text(), "precondition: the real writer emits the header"

        _run_uninstall(
            argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True, user_tier=False, keep_memory=False)
        )

        final = agents.read_text()
        assert _header() not in final
        assert START not in final
        assert final.startswith(user_prefix)
