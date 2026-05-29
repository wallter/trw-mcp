"""Tests for shared Cursor skills and hook script bootstrap helpers."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.integration
def test_cursor_skills_mirror_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror copies skill dirs to .cursor/skills/."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    source_dir = tmp_path / "fake_skills"
    (source_dir / "trw-deliver").mkdir(parents=True)
    (source_dir / "trw-deliver" / "SKILL.md").write_text("# trw-deliver", encoding="utf-8")

    result = generate_cursor_skills_mirror(tmp_path, ["trw-deliver"], source_dir=source_dir)
    dest = tmp_path / ".cursor" / "skills" / "trw-deliver" / "SKILL.md"

    assert dest.is_file()
    assert ".cursor/skills/trw-deliver" in result.get("created", [])


@pytest.mark.integration
def test_cursor_skills_mirror_preserves_user_skills(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror does NOT remove user skills outside the list."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    user_skill = tmp_path / ".cursor" / "skills" / "my-custom-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("my custom skill", encoding="utf-8")

    source_dir = tmp_path / "fake_skills"
    (source_dir / "trw-deliver").mkdir(parents=True)
    (source_dir / "trw-deliver" / "SKILL.md").write_text("# trw-deliver", encoding="utf-8")

    generate_cursor_skills_mirror(tmp_path, ["trw-deliver"], source_dir=source_dir)

    assert (user_skill / "SKILL.md").is_file()
    assert (user_skill / "SKILL.md").read_text() == "my custom skill"


@pytest.mark.integration
def test_cursor_skills_mirror_missing_source_warns_and_skips(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror skips missing source skills with a warning."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    source_dir = tmp_path / "empty_skills"
    source_dir.mkdir()

    result = generate_cursor_skills_mirror(tmp_path, ["nonexistent-skill"], source_dir=source_dir)

    assert result.get("created", []) == []
    assert not (tmp_path / ".cursor" / "skills" / "nonexistent-skill").exists()


@pytest.mark.integration
def test_cursor_hook_scripts_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts copies bundled scripts to .cursor/hooks/."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)
    (fake_data / "trw-session-start.sh").write_text("#!/usr/bin/env bash\necho hi", encoding="utf-8")

    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["trw-session-start.sh"])

    dest = tmp_path / ".cursor" / "hooks" / "trw-session-start.sh"
    assert dest.is_file()
    mode = stat.S_IMODE(os.stat(str(dest)).st_mode)
    assert mode & 0o111
    assert ".cursor/hooks/trw-session-start.sh" in result.get("created", [])


@pytest.mark.integration
def test_cursor_hook_scripts_idempotent_without_force(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts preserves existing scripts when force=False."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)
    (fake_data / "trw-stop.sh").write_text("#!/usr/bin/env bash\necho stop", encoding="utf-8")

    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"])
    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"])

    assert ".cursor/hooks/trw-stop.sh" in result.get("preserved", [])


@pytest.mark.integration
def test_cursor_hook_scripts_missing_bundled_script_warns(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts skips missing bundled scripts with a warning."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)

    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["nonexistent.sh"])

    assert result.get("created", []) == []
    assert not (tmp_path / ".cursor" / "hooks" / "nonexistent.sh").exists()


@pytest.mark.integration
def test_cursor_hook_scripts_force_overwrites_existing(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts with force=True overwrites existing scripts and reports updated."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)
    (fake_data / "trw-stop.sh").write_text("#!/usr/bin/env bash\necho v2", encoding="utf-8")

    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"])
    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"], force=True)

    assert ".cursor/hooks/trw-stop.sh" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_skills_mirror_force_removes_existing(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror with force=True removes and re-copies skill dirs."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    source_dir = tmp_path / "fake_skills"
    (source_dir / "trw-deliver").mkdir(parents=True)
    (source_dir / "trw-deliver" / "SKILL.md").write_text("# v2", encoding="utf-8")

    dest_skill = tmp_path / ".cursor" / "skills" / "trw-deliver"
    dest_skill.mkdir(parents=True)
    (dest_skill / "SKILL.md").write_text("# v1 (stale)", encoding="utf-8")
    (dest_skill / "stale-extra.md").write_text("stale", encoding="utf-8")

    result = generate_cursor_skills_mirror(tmp_path, ["trw-deliver"], source_dir=source_dir, force=True)

    updated = result.get("created", []) + result.get("updated", [])
    assert ".cursor/skills/trw-deliver" in updated
    assert not (dest_skill / "stale-extra.md").exists()
