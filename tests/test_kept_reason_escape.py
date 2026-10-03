"""Kept-file warnings escape control characters in file names (codex known issue on KEPT-REASONS-PRINTED)."""

from __future__ import annotations

from pathlib import Path


def test_printable_escapes_control_characters() -> None:
    from trw_mcp.bootstrap._utils import printable

    assert printable("plain/name.md") == "plain/name.md"
    assert printable("evil\x1b[2Jname.md") == "evil\\x1b[2Jname.md"
    assert "\n" not in printable("two\nlines")


def test_a_kept_file_with_a_control_character_name_is_escaped_in_the_warning(tmp_path: Path) -> None:
    """Red before: the sweep echoed the raw name, so a crafted file name could drive the terminal."""
    from trw_mcp.bootstrap._retire import retire_tree

    root = tmp_path / "proj"
    skill = root / ".claude" / "skills" / "trw-x"
    skill.mkdir(parents=True)
    (skill / "evil\x1b[2J.md").write_bytes(b"user\n")
    kept = [f"{p} ({why})" for p, why in retire_tree(skill, root, lambda _f: set()).kept]
    assert kept and all("\x1b" not in k for k in kept)
    assert any("\\x1b[2J.md" in k for k in kept)
