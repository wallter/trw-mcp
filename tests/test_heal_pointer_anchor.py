"""HEAL-POINTER-ANCHOR: the TRW block strip opens only at a start marker that stands alone on its line.

A prose line that merely BEGINS with ``<!-- trw:start -->`` used to open the strip region, so healing a
pointer deleted every user line between it and the real end marker (codex CORE-243-FR07 r1 P0).
"""

from __future__ import annotations

from pathlib import Path

_PROSE = "<!-- trw:start --> is the opening sentinel <!-- example -->"
_FIXTURE = f"@base.md\n{_PROSE}\n@security-policy.md\n<!-- trw:start -->\nSTALE TRW\n<!-- trw:end -->\n"


def test_strip_skips_a_prose_line_that_starts_with_the_marker() -> None:
    from trw_mcp.state.claude_md._orphan_strip import _strip_trw_section

    stripped, remaining = _strip_trw_section(_FIXTURE)
    assert stripped
    assert remaining == f"@base.md\n{_PROSE}\n@security-policy.md\n"


def test_strip_with_only_a_prose_mention_removes_nothing() -> None:
    from trw_mcp.state.claude_md._orphan_strip import _strip_trw_section

    content = f"@base.md\n{_PROSE}\n@security-policy.md\n<!-- trw:end -->\n"
    assert _strip_trw_section(content) == (False, content)


def test_heal_pointer_keeps_the_user_import(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._instruction_carrier import heal_pointer

    target = tmp_path / "AGENTS.md"
    target.write_text(_FIXTURE, encoding="utf-8")
    assert heal_pointer(target)
    text = target.read_text(encoding="utf-8")
    assert "@security-policy.md" in text and "STALE TRW" not in text


def test_merge_trw_section_heal_keeps_the_user_import(tmp_path: Path) -> None:
    """The default path that reached the bug before CORE-243-FR07: merge_trw_section's pointer guard heals."""
    from trw_mcp.state.claude_md._parser import merge_trw_section

    target = tmp_path / "CLAUDE.md"
    target.write_text(_FIXTURE, encoding="utf-8")
    merge_trw_section(target, "<!-- trw:start -->\nNEW\n<!-- trw:end -->\n", None)
    assert "@security-policy.md" in target.read_text(encoding="utf-8")


def test_legitimate_start_lines_still_strip() -> None:
    """Whole-line, CRLF, indented and collapsed ``START END`` markers keep working."""
    from trw_mcp.state.claude_md._orphan_strip import _strip_trw_section

    assert _strip_trw_section("a\n<!-- trw:start -->\nX\n<!-- trw:end -->\nb\n") == (True, "a\nb\n")
    assert _strip_trw_section("a\r\n<!-- trw:start -->\r\nX\r\n<!-- trw:end -->\r\nb\r\n")[0]
    assert _strip_trw_section("a\n  <!-- trw:start -->\nX\n<!-- trw:end -->\nb\n")[0]
    assert _strip_trw_section("a\n<!-- trw:start --> <!-- trw:end -->\nb\n") == (True, "a\nb\n")
