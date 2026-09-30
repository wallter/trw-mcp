"""Uninstall never leaves an unmodified SKILL.md behind because a user file shares its directory.

A leftover SKILL.md is a LIVE Claude Code skill after uninstall, pointing at removed TRW tools (dev36 canary
regression of dc8c25510). Real ``init_project`` + ``_run_uninstall`` round trips; tests/AGENTS.md rules 2, 3, 9.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = pytest.mark.integration

USER = b"user\n"


def _ns(project: Path, **overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {"target_dir": str(project), "dry_run": False, "yes": True, "user_tier": False}
    base.update(overrides)
    return argparse.Namespace(**base)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    return tmp_path


def _keys(project: Path) -> set[str]:
    data = yaml.safe_load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
    return set(data["content_hashes"])


def _user_bytes(root: Path) -> dict[str, bytes]:
    """Every file left under ``.claude/skills`` (the surface these tests exercise), for the whole-tree assertion."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted((root / ".claude" / "skills").rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def test_user_file_in_skill_dir_canary_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-audit"
    key = "trw-audit/SKILL.md"
    assert key in _keys(project)
    (skill / "my-notes.md").write_bytes(USER)

    _run_uninstall(_ns(project, ide="claude-code"))  # exit 0: no SystemExit

    assert not (skill / "SKILL.md").exists()
    assert not (skill / "audit-framework.md").exists()
    assert (skill / "my-notes.md").read_bytes() == USER and skill.is_dir()
    assert key not in _keys(project), "only a user-owned file remains: the record is dropped"
    assert "my-notes.md" in capsys.readouterr().out  # the Preserved line stays
    assert _user_bytes(project) == {".claude/skills/trw-audit/my-notes.md": USER}

    _run_uninstall(_ns(project, ide="claude-code"))
    second = capsys.readouterr().out
    assert "Removed 0 item(s)" in second and "trw-audit" not in second  # nothing TRW-owned left to report
    assert _user_bytes(project) == {".claude/skills/trw-audit/my-notes.md": USER}


def test_whole_project_uninstall_leaves_no_live_skill(tmp_path: Path) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-audit"
    (skill / "my-notes.md").write_bytes(USER)

    _run_uninstall(_ns(project))

    assert not (skill / "SKILL.md").exists()
    assert _user_bytes(project) == {".claude/skills/trw-audit/my-notes.md": USER}


def test_nested_user_dir_in_a_skill_survives_and_skill_md_goes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-ceremony-guide"
    nested = skill / "my-refs" / "deep"
    nested.mkdir(parents=True)
    (nested / "n.md").write_bytes(USER)
    (skill / "my-refs" / "top.md").write_bytes(USER + b"2")

    _run_uninstall(_ns(project, ide="claude-code"))

    assert not (skill / "SKILL.md").exists()
    assert _user_bytes(project) == {
        ".claude/skills/trw-ceremony-guide/my-refs/deep/n.md": USER,
        ".claude/skills/trw-ceremony-guide/my-refs/top.md": USER + b"2",
    }
    assert "trw-ceremony-guide/SKILL.md" not in _keys(project)


def test_edited_skill_md_with_a_user_file_is_kept_and_the_record_stays(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-audit"
    key = "trw-audit/SKILL.md"
    edited = (skill / "SKILL.md").read_bytes() + b"\n<!-- my edit -->\n"
    (skill / "SKILL.md").write_bytes(edited)
    (skill / "my-notes.md").write_bytes(USER)
    framework = (skill / "audit-framework.md").read_bytes()

    _run_uninstall(_ns(project, ide="claude-code"))

    out = capsys.readouterr().out
    # The plan already marks a skill whose recorded SKILL.md was edited "preserved-edited": the dir is left whole.
    assert "Preserved (edited): .claude/skills/trw-audit" in out
    assert _user_bytes(project) == {
        ".claude/skills/trw-audit/SKILL.md": edited,
        ".claude/skills/trw-audit/audit-framework.md": framework,
        ".claude/skills/trw-audit/my-notes.md": USER,
    }
    assert key in _keys(project)


def test_failed_skill_md_unlink_is_an_error_keeps_the_record_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-audit"
    key = "trw-audit/SKILL.md"
    (skill / "my-notes.md").write_bytes(USER)
    shipped = (skill / "SKILL.md").read_bytes()
    from trw_mcp.bootstrap import _uninstall_skill_dir
    from trw_mcp.bootstrap._trash import Removal

    real = _uninstall_skill_dir.remove_if_hash

    def refuse(path: Path, root: Path, expected: str, *, key: str | None = None) -> Removal:
        if path == skill / "SKILL.md":
            return Removal(key, path, "kept", None, None, "error removing: boom")
        return real(path, root, expected, key=key)

    monkeypatch.setattr(_uninstall_skill_dir, "remove_if_hash", refuse)  # the delete itself fails

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project, ide="claude-code"))

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "boom" in captured.out + captured.err
    assert key in _keys(project)
    assert _user_bytes(project) == {
        ".claude/skills/trw-audit/SKILL.md": shipped,
        ".claude/skills/trw-audit/my-notes.md": USER,
    }


def test_rerun_converges_after_an_edited_sibling_is_removed_by_the_user(tmp_path: Path) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-audit"
    key = "trw-audit/SKILL.md"
    sibling = skill / "audit-framework.md"
    edited = sibling.read_bytes() + b"\nmine\n"
    sibling.write_bytes(edited)

    _run_uninstall(_ns(project, ide="claude-code"))
    assert not (skill / "SKILL.md").exists()
    assert sibling.read_bytes() == edited and key in _keys(project)

    _run_uninstall(_ns(project, ide="claude-code"))  # still edited: nothing changes, record still kept
    assert sibling.read_bytes() == edited and key in _keys(project)

    sibling.unlink()  # the user deletes their edited copy
    _run_uninstall(_ns(project, ide="claude-code"))
    assert key not in _keys(project), "no TRW-owned file remains: the record is dropped"
    assert not skill.exists()


def test_record_is_dropped_when_skill_md_and_dir_are_both_gone(tmp_path: Path) -> None:
    project = _project(tmp_path)
    skill = project / ".claude" / "skills" / "trw-audit"
    (skill / "audit-framework.md").write_bytes(b"x")
    (skill / "audit-framework.md").unlink()
    (skill / "SKILL.md").unlink()
    skill.rmdir()

    _run_uninstall(_ns(project, ide="claude-code"))

    assert "trw-audit/SKILL.md" not in _keys(project)
