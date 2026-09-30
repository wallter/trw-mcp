"""PRD-CORE-243-FR07: the opencode AGENTS.md writer and the shared merge helper honour the pointer-skip guard.

A single-source pointer (a file whose only substantive line is ``@CLAUDE.md``) is the user's chosen layout;
appending a TRW block to it turns the pointer into content. ``merge_trw_section`` already skips it; these
two writers did not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_POINTER = "# Instructions\n\n@CLAUDE.md\n"


def _assert_pointer_kept(path: Path) -> None:
    from trw_mcp.state.claude_md._instruction_carrier import InstructionFileClass, classify_instruction_file

    assert path.read_text(encoding="utf-8") == _POINTER
    assert classify_instruction_file(path).kind is InstructionFileClass.POINTER


@pytest.mark.parametrize("force", [False, True])
def test_opencode_agents_md_writer_skips_a_pointer(tmp_path: Path, force: bool) -> None:
    from trw_mcp.bootstrap._opencode import generate_agents_md

    agents = tmp_path / "AGENTS.md"
    agents.write_text(_POINTER, encoding="utf-8")
    _assert_pointer_kept(agents)

    for _ in range(2):  # a second run is byte-identical too
        result = generate_agents_md(tmp_path, force=force)
        assert result["errors"] == []
        assert "AGENTS.md" in result["preserved"]
        _assert_pointer_kept(agents)


@pytest.mark.parametrize("force", [False, True])
def test_merge_helper_skips_a_pointer(tmp_path: Path, force: bool) -> None:
    from trw_mcp.bootstrap._file_ops import write_instruction_file_with_merge

    target = tmp_path / "ANTIGRAVITY.md"
    target.write_text(_POINTER, encoding="utf-8")

    for _ in range(2):
        result: dict[str, list[str]] = {}
        write_instruction_file_with_merge(
            target_path=target,
            rel_path="ANTIGRAVITY.md",
            trw_section="<!-- trw:start -->\nTRW\n<!-- trw:end -->\n",
            start_marker="<!-- trw:start -->",
            end_marker="<!-- trw:end -->",
            force=force,
            result=result,
        )
        assert result == {"preserved": ["ANTIGRAVITY.md"]}
        _assert_pointer_kept(target)


def test_merge_helper_still_writes_a_content_file(tmp_path: Path) -> None:
    """The guard skips pointers only: a hand-written file gains the TRW block below its own text."""
    from trw_mcp.bootstrap._file_ops import write_instruction_file_with_merge

    target = tmp_path / "ANTIGRAVITY.md"
    target.write_text("# Mine\n\nUse tabs.\n", encoding="utf-8")
    result: dict[str, list[str]] = {}
    write_instruction_file_with_merge(
        target_path=target,
        rel_path="ANTIGRAVITY.md",
        trw_section="<!-- trw:start -->\nTRW\n<!-- trw:end -->\n",
        start_marker="<!-- trw:start -->",
        end_marker="<!-- trw:end -->",
        force=False,
        result=result,
    )
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nUse tabs.\n") and "TRW" in text
    assert result.get("errors") in (None, [])


# Codex r1 P0: a prose line that merely starts with the start marker, followed by a user import. Healing cut
# there and deleted ``@security-policy.md``; the writers must leave the file byte-identical instead.
_PROSE_MARKER = (
    "@base.md\n<!-- trw:start --> is the opening sentinel <!-- example -->\n@security-policy.md\n"
    "<!-- trw:start -->\nSTALE TRW\n<!-- trw:end -->\n"
)


def test_opencode_writer_never_heals_away_a_user_import(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._opencode import generate_agents_md

    agents = tmp_path / "AGENTS.md"
    agents.write_text(_PROSE_MARKER, encoding="utf-8")
    generate_agents_md(tmp_path)
    assert "@security-policy.md" in agents.read_text(encoding="utf-8")


def test_merge_helper_never_heals_away_a_user_import(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._file_ops import write_instruction_file_with_merge

    target = tmp_path / "ANTIGRAVITY.md"
    target.write_text(_PROSE_MARKER, encoding="utf-8")
    write_instruction_file_with_merge(
        target_path=target,
        rel_path="ANTIGRAVITY.md",
        trw_section="<!-- trw:start -->\nTRW\n<!-- trw:end -->\n",
        start_marker="<!-- trw:start -->",
        end_marker="<!-- trw:end -->",
        force=False,
        result={},
    )
    assert "@security-policy.md" in target.read_text(encoding="utf-8")
