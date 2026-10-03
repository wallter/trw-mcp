"""UPDATE-PRESERVE-SKIPWT: does update-project keep a local edit to a managed file git is told to ignore (--skip-worktree / --assume-unchanged)?

``git status`` is silent about such a file whatever its bytes, so ``git_dirty_paths`` never lists it and ``preserve_uncommitted_changes`` would not put it back after a
writer overwrote it. The only other protection is each writer's manifest-hash check (the file differs from what TRW recorded). These tests run a REAL init + update-project
over a flagged, edited file and assert the user's bytes survive, for each flag and for each managed surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _DATA_DIR, init_project, update_project

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def committed_install(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]
    assert not update_project(root)["errors"]
    git(root, "add", "-A")
    git(root, *_NO_HOOKS, "commit", "-qm", "installed")
    return root


def next_bundle(tmp_path: Path) -> Path:
    """Bundle N+1: a new settings.json env key and a changed hook, skill and agent."""
    bundle = tmp_path / "bundle"
    shutil.copytree(_DATA_DIR, bundle)
    settings = bundle / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data.setdefault("env", {})["TRW_BUNDLE_NEXT"] = "1"
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    for rel in ("hooks/session-start.sh", "skills/trw-deliver/SKILL.md", "agents/trw-implementer.md"):
        target = bundle / rel
        target.write_text(target.read_text(encoding="utf-8") + "\n# bundle N+1\n", encoding="utf-8")
    return bundle


SURFACES = {
    "settings": ".claude/settings.json",
    "hook": ".claude/hooks/session-start.sh",
    "skill": ".claude/skills/trw-deliver/SKILL.md",
    "agent": ".claude/agents/trw-implementer.md",
    # The root canon copies: a user's edit is kept ONLY because git lists the file as dirty (their own receipt proves TRW's bytes, never the user's).
    "canon_framework": "FRAMEWORK.md",
    "canon_aaref": "AARE-F-FRAMEWORK.md",
}


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_a_flagged_and_edited_managed_file_keeps_the_users_bytes_through_an_update(
    tmp_path: Path, flag: str, surface: str
) -> None:
    rel = SURFACES[surface]
    repo = committed_install(tmp_path / "repo")
    path = repo / rel
    text = path.read_text(encoding="utf-8")
    # settings.json is JSON: a valid edit adds a key (an appended comment would make it unparseable, a different case).
    edited = (
        text.replace('"env": {', '"env": {\n    "MY_OWN_FLAG": "1",', 1)
        if rel.endswith(".json")
        else text + "\n# MY OWN LOCAL EDIT, git is told to ignore this file\n"
    )
    assert edited != text, "fixture: the edit must change the file"
    git(repo, "update-index", flag, rel)
    path.write_text(edited, encoding="utf-8")
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", rel], capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == "", "fixture: git must report nothing for the flagged, edited file"

    result = update_project(repo, data_dir=next_bundle(tmp_path))

    assert not result["errors"], result["errors"]
    assert path.read_text(encoding="utf-8") == edited, (
        f"{rel} lost the user's bytes: updated={rel in result['updated']} preserved={result.get('preserved')}"
    )


# ── the dirty check itself: git status is silent about a flagged file, so it must be compared by bytes (as the retire fix does) ──


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "plain"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("committed\n")
    (repo / "b.txt").write_text("committed\n")
    (repo / "c.txt").write_text("committed\n")
    git(repo, "add", "-A")
    git(repo, *_NO_HOOKS, "commit", "-qm", "base")
    return repo


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_a_flagged_file_whose_bytes_differ_from_what_git_holds_is_dirty(tmp_path: Path, flag: str) -> None:
    from trw_mcp.bootstrap._version_manifest import git_dirty_paths

    repo = _init_repo(tmp_path)
    for name in ("a.txt", "b.txt"):
        git(repo, "update-index", flag, name)
    (repo / "a.txt").write_text("a local edit\n")  # flagged and edited: git status says nothing
    # b.txt is flagged and unchanged; c.txt is neither

    dirty = git_dirty_paths(repo, ["a.txt", "b.txt", "c.txt"])

    assert dirty == {"a.txt"}


def test_a_flagged_file_that_is_absent_is_not_reported_dirty(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._version_manifest import git_dirty_paths

    repo = _init_repo(tmp_path)
    git(repo, "update-index", "--skip-worktree", "a.txt")
    (repo / "a.txt").unlink()  # a sparse-checkout style absence is not a user edit

    assert git_dirty_paths(repo, ["a.txt"]) == set()


def test_a_flagged_file_git_cannot_compare_is_treated_as_dirty_not_clean(tmp_path: Path) -> None:
    """Fail safe: when the comparison cannot be made, the file is protected."""
    from unittest.mock import patch

    from trw_mcp.bootstrap import _git_flagged
    from trw_mcp.bootstrap._version_manifest import git_dirty_paths

    repo = _init_repo(tmp_path)
    git(repo, "update-index", "--skip-worktree", "a.txt")
    with patch.object(_git_flagged, "_git_restores_bytes", return_value=None):
        assert git_dirty_paths(repo, ["a.txt"]) == {"a.txt"}


def test_a_flagged_file_that_cannot_be_read_is_dirty_and_never_raises(tmp_path: Path) -> None:
    """Fail safe: a flagged file whose read fails (permissions, a race) is protected, and the read error does not abort the update's preparation."""
    from unittest.mock import patch

    from trw_mcp.bootstrap._version_manifest import git_dirty_paths

    repo = _init_repo(tmp_path)
    git(repo, "update-index", "--skip-worktree", "a.txt")
    real_read_bytes = Path.read_bytes

    def refuse_a(self: Path) -> bytes:
        if self.name == "a.txt":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", refuse_a):
        assert git_dirty_paths(repo, ["a.txt"]) == {"a.txt"}


# ── fail SAFE when git cannot answer (C1 red-team of L3-PRESERVE-SKIPWT-1-r3: an ls-files failure was read as "nothing flagged") ──


def _fail_git(monkeypatch: pytest.MonkeyPatch, verb: str | None, how: str) -> None:
    """Make every ``git ... <verb> ...`` call (every git call when *verb* is None) fail: exit 128, a timeout, or no git binary."""
    real_run = subprocess.run

    def fake(cmd: object, *args: object, **kwargs: object) -> object:
        if isinstance(cmd, list) and cmd and cmd[0] == "git" and (verb is None or verb in cmd):
            if how == "timeout":
                raise subprocess.TimeoutExpired(cmd, 5)
            if how == "missing":
                raise FileNotFoundError(2, "No such file or directory", "git")
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: injected failure")
        return real_run(cmd, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", fake)


@pytest.mark.parametrize("how", ["exit128", "timeout", "missing"])
def test_when_the_flag_census_cannot_answer_every_managed_file_counts_as_possibly_edited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    from trw_mcp.bootstrap._version_manifest import git_dirty_paths

    repo = _init_repo(tmp_path)
    git(repo, "update-index", "--skip-worktree", "a.txt")
    (repo / "a.txt").write_text("a local edit git is told to ignore\n")
    _fail_git(monkeypatch, "ls-files", how)

    dirty = git_dirty_paths(repo, ["a.txt", "b.txt", "c.txt"])

    assert dirty is not None and {"a.txt", "b.txt", "c.txt"} <= dirty, f"the census failure was read as clean: {dirty}"


@pytest.mark.parametrize("how", ["exit128", "timeout", "missing"])
def test_when_git_status_itself_fails_the_answer_stays_unknown_so_the_caller_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """NFR01: unlike a census that fails while status works (silent, so it fails safe above), a failing ``status`` is reported loudly as unknown
    ("git status unavailable: ... only the managed-artifacts hash guard protects edited files"); test_update_transaction_runtime_preservation pins the
    warning and the completed update."""
    from trw_mcp.bootstrap._version_manifest import git_dirty_paths

    repo = _init_repo(tmp_path)
    _fail_git(monkeypatch, None if how == "missing" else "status", how)

    assert git_dirty_paths(repo, ["a.txt", "b.txt"]) is None


@pytest.mark.parametrize("how", ["exit128", "timeout"])
def test_a_flagged_and_edited_canon_copy_survives_an_update_even_when_the_flag_census_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    repo = committed_install(tmp_path / "repo")
    path = repo / "FRAMEWORK.md"
    edited = path.read_text(encoding="utf-8") + "\n<!-- MY LOCAL EDIT -->\n"
    git(repo, "update-index", "--skip-worktree", "FRAMEWORK.md")
    path.write_text(edited, encoding="utf-8")
    bundle = next_bundle(tmp_path)
    _fail_git(monkeypatch, "ls-files", how)

    result = update_project(repo, data_dir=bundle)

    assert not result["errors"], result["errors"]
    assert path.read_text(encoding="utf-8") == edited
