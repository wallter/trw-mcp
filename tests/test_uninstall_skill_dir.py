"""Skill-directory uninstall deletes only files proven TRW's; user bytes survive (tests/AGENTS.md rules 1-3, 9)."""

from __future__ import annotations

import hashlib
import inspect
import os
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from trw_mcp.bootstrap import _uninstall_skill_dir as sd  # the skill-dir walk lives here
from trw_mcp.bootstrap._trash import Removal
from trw_mcp.bootstrap._uninstall_manifest import KeyDisposition, apply_removal

SKILL_MD = b"# demo skill\n"
REF_MD = b"reference\n"
USER = b"user notes\n"


class Env(NamedTuple):
    root: Path
    skill: Path
    outside: Path
    key: str


def _tree(root: Path) -> dict[str, bytes]:
    # apply_removal leaves each proven capture in .trw/trash (uninstall moves them to the system Trash in one
    # step, UNINSTALL-SKILL-DIR-CAPTURE), so the project tree is judged without that holding area.
    trash = root / ".trw" / "trash"
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and trash not in p.parents
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    data = tmp_path / "data"
    bundled = data / "skills" / "demo"
    (bundled / "refs").mkdir(parents=True)
    (bundled / "SKILL.md").write_bytes(SKILL_MD)
    (bundled / "refs" / "a.md").write_bytes(REF_MD)
    monkeypatch.setattr("trw_mcp.bootstrap._utils._DATA_DIR", data)
    root = tmp_path / "project"
    skill = root / ".claude" / "skills" / "demo"
    (skill / "refs").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(SKILL_MD)
    (skill / "refs" / "a.md").write_bytes(REF_MD)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_bytes(REF_MD)  # same bytes as TRW's file: deleting it would look "owned"
    return Env(root, skill, outside, ".claude/skills/demo/SKILL.md")


def _remove(env: Env, recorded: bytes | None = SKILL_MD) -> tuple[set[str], int, dict[str, list[str]]]:
    h = hashlib.sha256(recorded).hexdigest() if recorded is not None else ""
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal([KeyDisposition(env.key, env.skill, "remove", "", True, h)], result, env.root)
    return removed, errors, result


def test_untouched_skill_is_fully_removed_with_its_dirs(env: Env) -> None:
    removed, errors, result = _remove(env)
    assert removed == {env.key} and errors == 0 and not result.get("preserved")
    assert not env.skill.exists()
    assert (env.outside / "a.md").read_bytes() == REF_MD


@pytest.mark.parametrize("where", ["refs/user.md", "user.md"])
def test_user_file_inside_skill_dir_survives_and_the_record_is_dropped(env: Env, where: str) -> None:
    """Changed from dc8c25510: SKILL.md is deleted even though a user file stays (a leftover one is a live skill)."""
    (env.skill / where).write_bytes(USER)
    user = env.skill / where
    removed, errors, result = _remove(env)
    assert removed == {env.key} and errors == 0  # only user-owned files remain: nothing TRW-owned to track
    assert user.read_bytes() == USER
    assert any(str(user) in line and "user-added" in line for line in result["preserved"])
    assert not (env.skill / "SKILL.md").exists()
    assert _tree(env.root) == {str(user.relative_to(env.root)): USER}
    assert (env.outside / "a.md").read_bytes() == REF_MD


def test_user_overwritten_skill_md_survives_and_keeps_the_record(env: Env) -> None:
    (env.skill / "SKILL.md").write_bytes(USER)
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0
    assert _tree(env.root) == {".claude/skills/demo/SKILL.md": USER}
    assert any("SKILL.md" in line and "edited" in line for line in result["preserved"])


def test_edited_trw_file_is_kept_but_untouched_siblings_go(env: Env) -> None:
    (env.skill / "refs" / "a.md").write_bytes(b"edited\n")
    removed, _, result = _remove(env)
    assert removed == set()
    assert (env.skill / "refs" / "a.md").read_bytes() == b"edited\n"
    # The unmodified SKILL.md goes even though an edited sibling stays; the record stays for the edited TRW file.
    assert _tree(env.root) == {".claude/skills/demo/refs/a.md": b"edited\n"}
    assert len(result["preserved"]) == 1 and "edited (hash differs)" in result["preserved"][0]


def test_skill_md_edited_after_planning_is_kept_by_recorded_hash(env: Env) -> None:
    (env.skill / "SKILL.md").write_bytes(b"# user rewrite\n")
    removed, _, _ = _remove(env)
    assert removed == set()
    assert (env.skill / "SKILL.md").read_bytes() == b"# user rewrite\n"


def test_symlink_inside_skill_dir_is_not_followed(env: Env, tmp_path: Path) -> None:
    (env.skill / "link.md").symlink_to(env.outside / "a.md")
    (env.skill / "linkdir").symlink_to(env.outside, target_is_directory=True)
    removed, _, result = _remove(env)
    assert removed == set()
    assert (env.skill / "link.md").is_symlink() and (env.skill / "linkdir").is_symlink()
    assert (env.outside / "a.md").read_bytes() == REF_MD
    assert len(result["preserved"]) == 2


def test_intermediate_dir_swapped_for_symlink_during_walk_deletes_nothing_outside(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = sd._not_owned_reason
    swapped: list[bool] = []

    def swap_after_proof(path: Path, rel: Path, bundled_root: Path, recorded_hash: str) -> str | None:
        verdict = real(path, rel, bundled_root, recorded_hash)
        if rel == Path("refs/a.md") and not swapped:
            (env.skill / "refs").rename(tmp_path / "moved-refs")
            (env.skill / "refs").symlink_to(env.outside, target_is_directory=True)
            swapped.append(True)
        return verdict

    monkeypatch.setattr(sd, "_not_owned_reason", swap_after_proof)
    removed, errors, _ = _remove(env)
    assert errors >= 1  # the refusal is a failure, not a quiet keep
    assert swapped == [True]  # the interleaving actually ran
    assert removed == set()
    assert (env.outside / "a.md").read_bytes() == REF_MD
    assert (env.skill / "refs").is_symlink()
    assert (tmp_path / "moved-refs" / "a.md").read_bytes() == REF_MD


def test_unreadable_file_is_kept(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open
    target = env.skill / "refs" / "a.md"

    def deny(path: str | os.PathLike[str], *args: object, **kwargs: object) -> int:
        if Path(path) == target:
            raise PermissionError(13, "denied")
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny)
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0
    assert target.read_bytes() == REF_MD
    assert result["preserved"]


def test_unlistable_directory_is_kept(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    real_scandir = os.scandir
    refs = env.skill / "refs"

    def deny(path: str | os.PathLike[str]) -> object:
        if Path(path) == refs:
            raise PermissionError(13, "denied")
        return real_scandir(path)

    with monkeypatch.context() as patched:  # scoped: tmp_path's cleanup scans with the real os.scandir
        patched.setattr(os, "scandir", deny)
        removed, _, _ = _remove(env)
    assert removed == set()
    assert (refs / "a.md").read_bytes() == REF_MD


def test_rerun_after_a_kept_user_file_lists_it_again(env: Env) -> None:
    (env.skill / "user.md").write_bytes(USER)
    first_removed, _, first = _remove(env)
    assert first_removed == {env.key}  # record dropped: only a user file remains
    assert not (env.skill / "SKILL.md").exists()
    assert not (env.skill / "refs").exists()
    second_removed, errors, second = _remove(env)
    assert second_removed == {env.key} and errors == 0
    assert first["preserved"] == second["preserved"] and "user-added" in second["preserved"][0]
    assert _tree(env.root) == {".claude/skills/demo/user.md": USER}


@pytest.mark.parametrize("victim", ["refs/a.md", "SKILL.md"])
def test_unlink_failure_is_an_error_and_keeps_the_record(
    env: Env, monkeypatch: pytest.MonkeyPatch, victim: str
) -> None:
    real = sd.remove_if_hash

    def refuse(path: Path, root: Path, expected: str, *, key: str | None = None) -> Removal:
        if path == env.skill / victim:
            return Removal(key, path, "kept", None, None, "error removing: boom")
        return real(path, root, expected, key=key)

    monkeypatch.setattr(sd, "remove_if_hash", refuse)  # the delete itself fails; nothing else changes
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 1
    assert "boom" in result["errors"][0]
    assert (env.skill / victim).read_bytes() in (SKILL_MD, REF_MD)
    # SKILL.md is attempted even when another delete failed; only the victim remains.
    assert (env.skill / "SKILL.md").exists() == (victim == "SKILL.md")


def test_rmdir_error_other_than_not_empty_is_a_failure(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(path: str | os.PathLike[str]) -> None:
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patched:  # scoped: tmp_path's cleanup removes directories with the real os.rmdir
        patched.setattr(os, "rmdir", deny)
        removed, errors, _ = _remove(env)
    assert removed == set() and errors >= 1


def test_failing_skill_md_read_keeps_everything_that_matters(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = os.open
    target = env.skill / "SKILL.md"

    def deny(path: str | os.PathLike[str], *args: object, **kwargs: object) -> int:
        if Path(path) == target:
            raise PermissionError(13, "denied")
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny)
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0
    assert target.read_bytes() == SKILL_MD and "unreadable" in result["preserved"][-1]


def test_failing_bundled_read_keeps_the_file(env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_open = os.open
    shipped = tmp_path / "data" / "skills" / "demo" / "refs" / "a.md"

    def deny(path: str | os.PathLike[str], *args: object, **kwargs: object) -> int:
        if Path(path) == shipped:
            raise PermissionError(13, "denied")
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny)
    removed, _, _ = _remove(env)
    assert removed == set()
    assert (env.skill / "refs" / "a.md").read_bytes() == REF_MD


def test_fifo_in_skill_dir_is_kept_without_hanging(env: Env) -> None:
    fifo = env.skill / "pipe"
    os.mkfifo(fifo)
    removed, _, result = _remove(env)
    assert removed == set()
    assert fifo.exists() and any("not a regular file" in line for line in result["preserved"])
    assert not (env.skill / "SKILL.md").exists()  # unmodified SKILL.md goes; the unproven FIFO keeps the record


def test_skill_missing_from_the_bundle_deletes_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "emptydata"
    (empty / "skills").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.bootstrap._utils._DATA_DIR", empty)
    before = _tree(env.root)
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0
    assert _tree(env.root) == before
    assert all("not in the bundled skill" in line for line in result["preserved"])


def test_user_created_empty_subdir_is_kept(env: Env) -> None:
    (env.skill / "mine").mkdir()
    removed, _, result = _remove(env)
    assert removed == {env.key}  # a user-owned empty dir alone does not keep the record
    assert (env.skill / "mine").is_dir() and not (env.skill / "SKILL.md").exists()
    assert any("mine" in line and "not in the bundled skill" in line for line in result["preserved"])


def test_oversized_and_size_mismatched_files_are_kept_unread(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    (env.skill / "refs" / "a.md").write_bytes(REF_MD + b"x")  # size differs from the shipped copy
    monkeypatch.setattr(sd, "_MAX_HASH_BYTES", 4)
    removed, _, result = _remove(env)
    assert removed == set()
    assert (env.skill / "refs" / "a.md").read_bytes() == REF_MD + b"x"
    assert (env.skill / "SKILL.md").read_bytes() == SKILL_MD  # over the cap: never proven, so kept
    assert result["preserved"]


def test_deep_tree_does_not_recurse(env: Env) -> None:
    deep = env.skill
    for _ in range(300):
        deep = deep / "d"
    deep.mkdir(parents=True)
    (deep / "user.md").write_bytes(USER)
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(len(inspect.stack()) + 120)  # a recursive walk of 300 levels would blow this
    try:
        removed, errors, _ = _remove(env)
    finally:
        sys.setrecursionlimit(limit)
    assert removed == {env.key} and errors == 0
    assert (deep / "user.md").read_bytes() == USER


def test_shipped_lstat_error_other_than_absent_is_unreadable_and_keeps_the_record(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = os.lstat
    shipped = tmp_path / "data" / "skills" / "demo" / "refs" / "a.md"

    def deny(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == shipped:
            raise PermissionError(13, "denied")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", deny)
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0  # "unreadable" is TRW-side uncertainty, not a user file
    assert (env.skill / "refs" / "a.md").read_bytes() == REF_MD
    assert any("a.md" in line and "unreadable" in line for line in result["preserved"])


@pytest.mark.parametrize("kind", ["symlink", "dir"])
def test_skill_md_that_is_not_a_regular_file_is_reported_once(env: Env, tmp_path: Path, kind: str) -> None:
    (env.skill / "SKILL.md").unlink()
    if kind == "symlink":
        (env.skill / "SKILL.md").symlink_to(env.outside / "a.md")
    else:
        (env.skill / "SKILL.md").mkdir()
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0
    assert sum("SKILL.md" in line for line in result["preserved"]) == 1
    assert (env.outside / "a.md").read_bytes() == REF_MD


def test_skill_md_replaced_between_hash_and_delete_is_kept(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    target = env.skill / "SKILL.md"
    real = sd._not_owned_reason
    user = b"# user swapped this in\n"

    def racing(path: Path, rel: Path, bundled_root: Path, recorded_hash: str) -> str | None:
        result = real(path, rel, bundled_root, recorded_hash)
        if path == target:  # atomic replace after the hash proved ownership
            tmp = env.skill / "tmp-swap"
            tmp.write_bytes(user)
            os.replace(tmp, target)
        return result

    monkeypatch.setattr(sd, "_not_owned_reason", racing)
    removed, errors, result = _remove(env)
    assert removed == set() and errors == 0
    assert target.read_bytes() == user
    assert any("changed during uninstall" in line for line in result["preserved"])
