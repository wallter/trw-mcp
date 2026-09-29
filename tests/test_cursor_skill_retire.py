"""Canary follow-up: a kept file's reason for a disabled optional skill reaches the printed warnings.

The per-file reasons used to go only into ``preserved``, which the CLI summarises as a count, so a user never
saw why a file was kept. The cursor-mirror retire itself is pinned here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_disabled_trw_assess_cursor_mirror_is_retired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The unmodified cursor mirror of a disabled skill is retired (passes on trunk too; pinned as a guard)."""
    from trw_mcp.bootstrap import _optional_skills
    from trw_mcp.bootstrap._cursor import cursor_skill_mirror_contents
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills

    root = tmp_path / "proj"
    root.mkdir()
    for rel, data in cursor_skill_mirror_contents(["trw-assess"]).items():  # an installed, unmodified mirror
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    skill_md = root / ".cursor" / "skills" / "trw-assess" / "SKILL.md"
    assert skill_md.is_file()
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda n, *_a, **_k: n != "trw-assess")
    result = generate_cursor_ide_skills(root)
    assert not skill_md.exists()
    assert ".cursor/skills/trw-assess" in result.get("removed", [])


def test_an_edited_cursor_mirror_is_kept_and_the_reason_is_a_printed_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _optional_skills
    from trw_mcp.bootstrap._cursor import cursor_skill_mirror_contents
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills

    root = tmp_path / "proj"
    root.mkdir()
    for rel, data in cursor_skill_mirror_contents(["trw-assess"]).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    skill_md = root / ".cursor" / "skills" / "trw-assess" / "SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + b"\nmy note\n")
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda n, *_a, **_k: n != "trw-assess")
    result = generate_cursor_ide_skills(root)
    assert skill_md.read_bytes().endswith(b"\nmy note\n")
    assert any(w.startswith(".cursor/skills/trw-assess/SKILL.md") for w in result.get("warnings", []))
