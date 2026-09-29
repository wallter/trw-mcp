"""Deleting through a user symlink must never leave the checkout (FS-LINT triage P0-S).

Two sites deleted with no symlink gate: ``generate_cursor_skills_mirror(force=True)``
(``rmtree`` of ``.cursor/skills/<name>``) and ``enforce_tombstones`` (``unlink`` /
prune of a tombstoned path). A user who symlinks ``.cursor/skills`` or ``.claude/hooks``
to a shared directory would have had the SHARED directory's bytes deleted. Each case
asserts the outside bytes survive, the refusal is reported, and (non-vacuity) the same
call still removes a plain, un-redirected target.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror
from trw_mcp.bootstrap._tombstones import enforce_tombstones

pytestmark = pytest.mark.integration

_HOOK_KEY = "post-compact.sh"


def _outside_tree(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    outside = tmp_path / "shared"
    (outside / "trw-deliver").mkdir(parents=True)
    (outside / "trw-deliver" / "SKILL.md").write_bytes(b"# the user's shared skill\n")
    (outside / "trw-deliver" / "notes.md").write_bytes(b"hand notes\n")
    (outside / "post-compact.sh").write_bytes(b"#!/bin/sh\necho mine\n")
    return outside, {str(p.relative_to(outside)): p.read_bytes() for p in outside.rglob("*") if p.is_file()}


def _bytes_of(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "bundle"
    (source / "trw-deliver").mkdir(parents=True)
    (source / "trw-deliver" / "SKILL.md").write_text("# bundled", encoding="utf-8")
    return source


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_force_mirror_never_rmtrees_through_a_symlinked_skills_root(tmp_path: Path) -> None:
    outside, before = _outside_tree(tmp_path)
    repo = _repo(tmp_path)
    (repo / ".cursor").mkdir()
    (repo / ".cursor" / "skills").symlink_to(outside, target_is_directory=True)

    result = generate_cursor_skills_mirror(repo, ["trw-deliver"], source_dir=_source(tmp_path), force=True)

    assert _bytes_of(outside) == before, "force=True deleted or rewrote the symlink target's bytes"
    assert ".cursor/skills/trw-deliver" in result["preserved"]
    assert ".cursor/skills/trw-deliver" not in result["created"] + result["updated"]


def test_force_mirror_never_rmtrees_a_symlinked_skill_dir(tmp_path: Path) -> None:
    outside, before = _outside_tree(tmp_path)
    repo = _repo(tmp_path)
    skills = repo / ".cursor" / "skills"
    skills.mkdir(parents=True)
    (skills / "trw-deliver").symlink_to(outside / "trw-deliver", target_is_directory=True)

    result = generate_cursor_skills_mirror(repo, ["trw-deliver"], source_dir=_source(tmp_path), force=True)

    assert _bytes_of(outside) == before
    assert (skills / "trw-deliver").is_symlink(), "the user's link was replaced"
    assert ".cursor/skills/trw-deliver" in result["preserved"]


def test_force_mirror_still_replaces_a_plain_skill_dir(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    plain = repo / ".cursor" / "skills" / "trw-deliver"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text("# stale", encoding="utf-8")
    (plain / "stale-extra.md").write_text("stale", encoding="utf-8")

    result = generate_cursor_skills_mirror(repo, ["trw-deliver"], source_dir=_source(tmp_path), force=True)

    assert ".cursor/skills/trw-deliver" in result["created"] + result["updated"]
    assert not (plain / "stale-extra.md").exists(), "non-vacuity: force must still discard a plain dir"
    assert (plain / "SKILL.md").read_text(encoding="utf-8") == "# bundled"


def test_tombstone_enforcement_never_unlinks_through_a_symlinked_hooks_dir(tmp_path: Path) -> None:
    outside, before = _outside_tree(tmp_path)
    repo = _repo(tmp_path)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "hooks").symlink_to(outside, target_is_directory=True)
    result: dict[str, list[str]] = {"errors": []}

    enforce_tombstones(repo, {_HOOK_KEY}, result)

    assert _bytes_of(outside) == before, "the shared hooks dir lost a file to a tombstone"
    assert result["errors"] == [], "a refusal is a preserve, not an error that rolls the update back"
    assert any(_HOOK_KEY in note and "symlink" in note for note in result["preserved"]), result


def test_tombstone_enforcement_keeps_a_symlinked_leaf_and_its_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hooks = repo / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    user_file = tmp_path / "mine.sh"
    user_file.write_bytes(b"#!/bin/sh\necho mine\n")
    (hooks / _HOOK_KEY).symlink_to(user_file)
    result: dict[str, list[str]] = {"errors": []}

    enforce_tombstones(repo, {_HOOK_KEY}, result)

    assert (hooks / _HOOK_KEY).is_symlink(), "the user's link was removed"
    assert user_file.read_bytes() == b"#!/bin/sh\necho mine\n"
    assert result["errors"] == []


def test_tombstone_enforcement_still_removes_a_plain_recreated_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hooks = repo / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / _HOOK_KEY).write_bytes(b"recreated by this run\n")
    result: dict[str, list[str]] = {"errors": []}

    enforce_tombstones(repo, {_HOOK_KEY}, result)

    assert not (hooks / _HOOK_KEY).exists(), "non-vacuity: a plain recreated tombstoned file must still be removed"
    assert result["errors"] == []
    assert any("kept deleted" in note for note in result["info"])
