"""Retired TRW files are deleted in place, never captured into ``.trw/trash`` (Feature A).

The rule (``bootstrap/_retire.py``): TRW's recorded bytes go; an edit git already holds (tracked, clean) goes
too and is named with its restore command; an uncommitted edit, an edited untracked file, and non-TRW bytes in a
project that is not in git are KEPT (HB-2) and named with the command that removes them.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _retire as _retire_mod
from trw_mcp.bootstrap._retire import git_recoverable, record_retirement, retire_file, retire_tree

_TRW = b"# bytes TRW wrote\n"
_EDIT = b"# bytes the user edited\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        env=env,
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    return root


def _agent(root: Path, data: bytes) -> Path:
    path = root / ".claude" / "agents" / "trw-old.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "x")


def _no_trash(root: Path) -> bool:
    return not (root / ".trw" / "trash").exists()


def test_a_file_matching_trws_hash_is_deleted_in_place_without_trash(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    path = _agent(root, _TRW)
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "removed"
    assert not path.exists()
    assert _no_trash(root)


def test_a_tracked_clean_edited_file_is_deleted_in_place(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _agent(root, _EDIT)
    _commit(root)
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "git"
    assert not path.exists()
    assert _no_trash(root)
    _git(root, "restore", "--", ".claude/agents/trw-old.md")  # the promise: git brings it back
    assert path.read_bytes() == _EDIT


@pytest.mark.parametrize("how", ["modified", "staged"])
def test_a_tracked_file_with_uncommitted_edits_is_kept(tmp_path: Path, how: str) -> None:
    root = _repo(tmp_path)
    path = _agent(root, _TRW)
    _commit(root)
    path.write_bytes(_EDIT)
    if how == "staged":
        _git(root, "add", "--", ".claude/agents/trw-old.md")
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "kept"
    assert path.read_bytes() == _EDIT
    assert _no_trash(root)


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_an_edit_hidden_from_git_status_by_an_index_flag_is_kept(tmp_path: Path, flag: str) -> None:
    """``git status`` reports a skip-worktree / assume-unchanged file clean even when its bytes differ from
    HEAD, so "status is empty" never proves git can restore it: the user's bytes stay (HB-2)."""
    root = _repo(tmp_path)
    path = _agent(root, _TRW)
    _commit(root)
    _git(root, "update-index", flag, "--", ".claude/agents/trw-old.md")
    path.write_bytes(_EDIT)
    out = retire_file(path, root, {_sha(b"# some other TRW release\n")})
    assert out.status == "kept"
    assert path.read_bytes() == _EDIT
    assert git_recoverable(path, root, _sha(_EDIT)) is False


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_a_flagged_file_whose_bytes_match_head_still_retires_via_git(tmp_path: Path, flag: str) -> None:
    root = _repo(tmp_path)
    path = _agent(root, _EDIT)
    _commit(root)
    _git(root, "update-index", flag, "--", ".claude/agents/trw-old.md")
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "git"
    assert not path.exists()


def test_a_relative_root_keeps_a_hidden_edit_despite_head_bytes_one_level_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative *root* is never re-rooted by ``git -C``: a decoy at ``root/root/<rel>`` holding HEAD's bytes
    never proves the user's edited file recoverable."""
    root = _repo(tmp_path)
    path = _agent(root, _TRW)
    _commit(root)
    _git(root, "update-index", "--skip-worktree", "--", ".claude/agents/trw-old.md")
    path.write_bytes(_EDIT)
    _agent(root / "proj", _TRW)  # decoy at root/<relative path> holding HEAD's bytes
    monkeypatch.chdir(tmp_path)
    rel_root = Path("proj")
    out = retire_file(rel_root / ".claude" / "agents" / "trw-old.md", rel_root, {_sha(b"# other release\n")})
    assert out.status == "kept"
    assert path.read_bytes() == _EDIT


def test_bytes_a_lossy_clean_filter_hides_from_git_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean filter that drops a line makes ``status`` and ``hash-object`` agree with HEAD, but ``git restore``
    writes the stripped bytes back: the user's bytes on disk are not recoverable, so they stay (HB-2)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    root = _repo(tmp_path)
    _git(root, "config", "filter.strip.clean", "sed -e /SECRET/d")
    _git(root, "config", "filter.strip.smudge", "cat")
    (root / ".gitattributes").write_text("*.md filter=strip\n")
    on_disk = _EDIT + b"SECRET=hunter2\n"
    path = _agent(root, on_disk)
    _commit(root)
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "kept"
    assert path.read_bytes() == on_disk


_FAILURES: dict[str, tuple[str, BaseException | None]] = {
    "cat-file exits 128": ("cat-file", None),
    "rev-parse exits 128": ("rev-parse", None),
    "cat-file times out": ("cat-file", subprocess.TimeoutExpired("git", 5)),
    "git binary missing": ("git", FileNotFoundError(2, "No such file or directory", "git")),
}


@pytest.mark.parametrize("failure", sorted(_FAILURES))
def test_a_failing_git_blob_check_means_not_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root = _repo(tmp_path)
    path = _agent(root, _EDIT)
    _commit(root)
    assert _retire_mod.git_recoverable(path, root, _sha(_EDIT)) is True  # control: git does hold these bytes
    failing, exc = _FAILURES[failure]
    real_run = _retire_mod.subprocess.run

    def run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if failing in cmd:
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: simulated")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_retire_mod.subprocess, "run", run)
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "kept"
    assert path.read_bytes() == _EDIT


def test_a_committed_file_trw_never_recorded_is_kept(tmp_path: Path) -> None:
    """Git-clean alone is not ownership: a committed repo-local file with no TRW record is the user's own."""
    root = _repo(tmp_path)
    path = _agent(root, _EDIT)
    _commit(root)
    out = retire_file(path, root, set())
    assert out.status == "kept"
    assert path.read_bytes() == _EDIT


def test_an_untracked_edited_file_in_a_git_project_is_kept(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _agent(root, _EDIT)
    assert retire_file(path, root, {_sha(_TRW)}).status == "kept"
    assert path.read_bytes() == _EDIT


def test_an_ignored_edited_file_is_kept(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitignore").write_text(".claude/\n")
    path = _agent(root, _EDIT)
    assert retire_file(path, root, {_sha(_TRW)}).status == "kept"
    assert path.read_bytes() == _EDIT


def test_outside_git_an_edited_file_is_kept_and_an_unchanged_one_is_deleted(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    edited = _agent(root, _EDIT)
    assert retire_file(edited, root, {_sha(_TRW)}).status == "kept"
    assert edited.read_bytes() == _EDIT
    edited.write_bytes(_TRW)
    assert retire_file(edited, root, {_sha(_TRW)}).status == "removed"
    assert not edited.exists()
    assert _no_trash(root)


def test_a_missing_file_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    assert retire_file(root / "nope.md", root, {_sha(_TRW)}).status == "absent"


def test_a_symlink_is_kept_and_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    outside = tmp_path / "outside.md"
    outside.write_bytes(_TRW)
    link = root / ".claude" / "agents" / "trw-old.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert retire_file(link, root, {_sha(_TRW)}).status == "kept"
    assert link.is_symlink()
    assert outside.read_bytes() == _TRW


def _skill(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    skill = root / ".claude" / "skills" / "trw-old"
    (skill / "refs").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(_TRW)
    (skill / "refs" / "ref.md").write_bytes(b"ref\n")
    return root, skill


def _allow(*datas: bytes):  # type: ignore[no-untyped-def]
    return lambda _f: {_sha(d) for d in datas}


def test_a_proven_tree_and_its_directories_are_removed_without_trash(tmp_path: Path) -> None:
    root, skill = _skill(tmp_path)
    out = retire_tree(skill, root, _allow(_TRW, b"ref\n"))
    assert out.kept == []
    assert sorted(out.removed) == [".claude/skills/trw-old/SKILL.md", ".claude/skills/trw-old/refs/ref.md"]
    assert not skill.exists()
    assert _no_trash(root)


def test_an_edited_file_keeps_its_bytes_and_its_directory(tmp_path: Path) -> None:
    root, skill = _skill(tmp_path)
    (skill / "refs" / "ref.md").write_bytes(b"my edit\n")
    out = retire_tree(skill, root, _allow(_TRW, b"ref\n"))
    assert [p for p, _ in out.kept] == [".claude/skills/trw-old/refs/ref.md"]
    assert (skill / "refs" / "ref.md").read_bytes() == b"my edit\n"
    assert not (skill / "SKILL.md").exists()
    assert _no_trash(root)


def test_a_file_added_during_the_sweep_keeps_the_directory(tmp_path: Path) -> None:
    root, skill = _skill(tmp_path)

    def add_then_allow(_f: Path) -> set[str]:
        new = skill / "notes.md"
        if not new.exists():
            new.write_bytes(b"added after the listing\n")
        return {_sha(_TRW), _sha(b"ref\n")}

    out = retire_tree(skill, root, add_then_allow)
    assert (skill / "notes.md").read_bytes() == b"added after the listing\n"
    assert out.kept


def test_a_symlink_inside_the_tree_is_kept_and_not_followed(tmp_path: Path) -> None:
    root, skill = _skill(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_bytes(_TRW)
    (skill / "link.md").symlink_to(outside)
    out = retire_tree(skill, root, _allow(_TRW, b"ref\n"))
    assert [p for p, _ in out.kept] == [".claude/skills/trw-old/link.md"]
    assert (skill / "link.md").is_symlink()
    assert outside.read_bytes() == _TRW


def test_a_fifo_in_the_tree_is_kept_and_never_blocks(tmp_path: Path) -> None:
    root, skill = _skill(tmp_path)
    os.mkfifo(skill / "pipe")
    out = retire_tree(skill, root, _allow(_TRW, b"ref\n"))
    assert out.kept == [(".claude/skills/trw-old/pipe", "not a regular file")]
    assert (skill / "pipe").exists()


def test_a_file_over_the_size_cap_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _retire

    root, skill = _skill(tmp_path)
    monkeypatch.setattr(_retire, "_HASH_CAP", len(_TRW) - 1)
    out = retire_tree(skill / "SKILL.md", root, _allow(_TRW))
    assert out.kept[0][1].startswith("larger than the ")
    assert (skill / "SKILL.md").read_bytes() == _TRW


def test_an_inspection_error_keeps_the_artifact_and_reports_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, skill = _skill(tmp_path)

    def denied(self: Path, pattern: str, **_kw: object):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patch:  # scoped: pytest's own tmp_path cleanup uses rglob too
        patch.setattr(Path, "rglob", denied)
        out = retire_tree(skill, root, _allow(_TRW))
    assert out.kept == [(".claude/skills/trw-old", "could not inspect: [Errno 13] denied")]
    assert (skill / "SKILL.md").read_bytes() == _TRW


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_an_unreadable_file_is_kept_and_the_sweep_continues(tmp_path: Path) -> None:
    root, skill = _skill(tmp_path)
    locked = skill / "SKILL.md"
    locked.chmod(0)
    try:
        out = retire_tree(skill, root, _allow(_TRW, b"ref\n"))
    finally:
        locked.chmod(0o644)
    assert [p for p, _ in out.kept] == [".claude/skills/trw-old/SKILL.md"]
    assert out.kept[0][1].startswith("unreadable: ")
    assert not (skill / "refs" / "ref.md").exists()


def test_record_retirement_names_the_restore_and_the_removal_commands() -> None:
    result: dict[str, list[str]] = {}
    from trw_mcp.bootstrap._retire import Retirement

    record_retirement(
        result,
        Retirement(
            ["a/gone.md"], ["a/in git.md"], [("a/mine.md", "not TRW's unchanged bytes, and git does not hold it")]
        ),
    )
    assert result["retired"] == ["a/gone.md", "a/in git.md"]
    assert (
        "a/in git.md: removed; your version differs from TRW's but is committed in git (restore: git restore -- 'a/in git.md')"
        in result["warnings"]
    )
    assert any(
        w.startswith("a/mine.md (") and w.endswith("kept; to remove it yourself run: rm a/mine.md")
        for w in result["warnings"]
    )


# --- the sweeps that call it -----------------------------------------------------------------------------


def _sweep(root: Path, manifest: dict[str, str]) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}
    _remove_stale_client_artifacts(root, result, manifest_hashes=manifest)
    return result


_REL = ".cursor/agents/trw-gone.md"


def _cursor_agent(root: Path, data: bytes) -> Path:
    path = root / _REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_the_client_sweep_deletes_a_recorded_unchanged_agent_without_trash(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    path = _cursor_agent(root, _TRW)
    result = _sweep(root, {_REL: _sha(_TRW)})
    assert not path.exists()
    assert result["retired"] == [_REL]
    assert _no_trash(root)


def test_the_client_sweep_deletes_a_git_clean_edited_agent_and_names_the_restore(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _cursor_agent(root, _EDIT)
    _commit(root)
    result = _sweep(root, {_REL: _sha(_TRW)})
    assert not path.exists()
    assert (
        f"{_REL}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {_REL})"
        in result["warnings"]
    )
    assert _no_trash(root)


def test_the_client_sweep_keeps_an_uncommitted_edit_and_names_the_removal(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _cursor_agent(root, _TRW)
    _commit(root)
    path.write_bytes(_EDIT)
    result = _sweep(root, {_REL: _sha(_TRW)})
    assert path.read_bytes() == _EDIT
    assert f"{_REL} (not_installer_owned)" in result["preserved"]
    assert any(w.startswith(f"{_REL} (") and w.endswith(f"rm {_REL}") for w in result["warnings"])
    assert _no_trash(root)


def test_the_client_sweep_keeps_an_edited_agent_outside_git(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    path = _cursor_agent(root, _EDIT)
    result = _sweep(root, {_REL: _sha(_TRW)})
    assert path.read_bytes() == _EDIT
    assert any(w.endswith(f"rm {_REL}") for w in result["warnings"])


# --- --dry-run parity ------------------------------------------------------------------------------------


def _preview(root: Path, manifest: dict[str, str]) -> dict[str, list[str]]:
    """The sweep as ``update --dry-run`` runs it: against the scratch copy, which git knows nothing about."""
    from trw_mcp.bootstrap._update_transaction import run_in_scratch
    from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}

    def apply(scratch: Path) -> None:
        _remove_stale_client_artifacts(scratch, result, manifest_hashes=manifest)

    run_in_scratch(root, result, apply)
    return result


def test_the_preview_matches_the_real_run_on_a_tracked_clean_edited_file(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _cursor_agent(root, _EDIT)
    _commit(root)
    preview = _preview(root, {_REL: _sha(_TRW)})
    assert path.read_bytes() == _EDIT  # the preview never writes
    real = _sweep(root, {_REL: _sha(_TRW)})
    assert not path.exists()
    assert preview["retired"] == real["retired"] == [_REL]
    assert preview["warnings"] == real["warnings"]


def test_the_preview_keeps_an_uncommitted_edit_like_the_real_run(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    path = _cursor_agent(root, _TRW)
    _commit(root)
    path.write_bytes(_EDIT)
    preview = _preview(root, {_REL: _sha(_TRW)})
    real = _sweep(root, {_REL: _sha(_TRW)})
    assert "retired" not in preview
    assert "retired" not in real
    assert preview["warnings"] == real["warnings"]


# --- races and filename patterns (codex round 1) ----------------------------------------------------------

_SAVED = b"# saved by the editor between the decision and the delete\n"


@pytest.mark.parametrize("tracked_clean", [False, True])
def test_a_save_landing_between_the_decision_and_the_delete_survives_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tracked_clean: bool
) -> None:
    import trw_mcp.bootstrap._retire as retire

    root = _repo(tmp_path)
    path = _agent(root, _TRW if not tracked_clean else _EDIT)
    _commit(root)
    real = retire._bounded_sha256

    def hash_then_save(p: Path) -> tuple[str | None, str]:
        out = real(p)
        p.write_bytes(_SAVED)  # the editor saves right after TRW hashed the old bytes
        return out

    monkeypatch.setattr(retire, "_bounded_sha256", hash_then_save)
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "kept"
    assert path.read_bytes() == _SAVED


@pytest.mark.parametrize("name", ["old[.]md", "old?md", "old*", "[a-z]ld.md"])
def test_a_glob_named_untracked_edit_is_never_matched_by_a_tracked_clean_file(tmp_path: Path, name: str) -> None:
    root = _repo(tmp_path)
    tracked = root / ".claude" / "agents" / "old.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(_EDIT)
    (root / ".gitignore").write_text("*\n!.gitignore\n!.claude/\n!.claude/agents/\n!.claude/agents/old.md\n")
    _commit(root)
    mine = tracked.parent / name
    mine.write_bytes(_SAVED)  # git-ignored (status is silent about it), edited, never recorded as TRW's bytes
    out = retire_file(mine, root, {_sha(_TRW)})
    assert out.status == "kept"
    assert mine.read_bytes() == _SAVED
    assert tracked.read_bytes() == _EDIT


def test_a_refused_purge_is_reported_kept_in_trash_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.bootstrap._retire as retire

    root = tmp_path / "proj"
    path = _agent(root, _TRW)
    real = retire.delete_proven_unchanged_captures

    def edit_then_purge(r: Path, captures: list[Path]) -> tuple[int, list[tuple[Path, str]]]:
        captures[0].write_bytes(_SAVED)  # a held descriptor writes after the capture was verified
        return real(r, captures)

    monkeypatch.setattr(retire, "delete_proven_unchanged_captures", edit_then_purge)
    out = retire_file(path, root, {_sha(_TRW)})
    assert out.status == "kept"
    assert out.why.startswith("kept at .trw/trash/") and out.why.endswith("(changed during removal)")
    capture = next((root / ".trw" / "trash").glob("*/data"))
    assert capture.read_bytes() == _SAVED
    result: dict[str, list[str]] = {}
    retire.record_retirement(result, retire.as_retirement(".claude/agents/trw-old.md", out))
    assert "retired" not in result
    assert result["warnings"] == [f".claude/agents/trw-old.md: {out.why}"]
