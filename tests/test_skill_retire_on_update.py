"""Canary F1: update-project retires a disabled optional skill (it used to re-deploy it), like init-project."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.usefixtures("no_memory_daemon")
def test_update_project_retires_a_disabled_optional_skill_in_a_real_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _optional_skills, init_project, update_project

    root = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        check=True,
    )
    name = next(iter(_optional_skills.CONDITIONAL_SKILLS))
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda n, *_a, **_k: True)
    assert not init_project(root, ide="claude-code")["errors"]
    skill_md = root / ".claude" / "skills" / name / "SKILL.md"
    assert skill_md.is_file(), "enabled optional skill is installed"
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda n, *_a, **_k: n != name)
    counts = []
    for _ in range(2):  # a second update must neither re-deploy it nor grow the trash
        update_project(root, ide="claude-code")
        assert not skill_md.exists()
        counts.append(len(list((root / ".trw" / "trash").glob("*/data"))))
    assert counts[0] >= 1 and counts[1] == counts[0]


def test_retire_reports_each_trashed_file_for_the_uncommitted_changes_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r1: without a trashed entry the guard can restore a just-retired skill in a real repo."""
    from trw_mcp.bootstrap import _optional_skills

    root = tmp_path / "proj"
    dest_root = root / ".claude" / "skills"
    name = next(iter(_optional_skills.CONDITIONAL_SKILLS))
    (dest_root / name).mkdir(parents=True)
    (dest_root / name / "SKILL.md").write_bytes(b"shipped\n")
    (dest_root / name / "notes.md").write_bytes(b"mine\n")
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr("trw_mcp.bootstrap._client_skills.skill_files", lambda *_a, **_k: [("SKILL.md", b"shipped\n")])
    result: dict[str, list[str]] = {}
    _optional_skills.retire_disabled_skills(dest_root, tmp_path, result, ".claude/skills", project_root=root)
    assert result["trashed"] == [f".claude/skills/{name}/SKILL.md"]
