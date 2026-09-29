"""CLAUDE-WORKTREES-SYMLINK: a symlink (or nested worktree) under ``.claude/worktrees/`` never blocks update-project.

The transaction validation and snapshot prune ``.claude/worktrees/``, but git reports files there as
dirty, so ``preserve_uncommitted_changes`` used to reach ``_reject_symlink_path`` on them and abort the
run. Pruned paths must never be inspected, read, written or followed; a symlink under a NON-pruned
managed path stays a hard refusal. Runs are out of process (production path resolution).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from trw_memory.testing.daemon_reaper import daemon_env_passthrough, reap_daemons_under

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(600)]

_RUNNER = """
import json, sys
from pathlib import Path
from trw_mcp.bootstrap import init_project, update_project
target, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "init":
    print(json.dumps(init_project(target, ide="claude-code")))
else:
    result = update_project(target)
    print(json.dumps(result))
    sys.exit(1 if result["errors"] else 0)
"""
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _run(target: Path, mode: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_")} | daemon_env_passthrough()
    env.update(
        HOME=str(home),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        TRW_EMBEDDINGS_ENABLED="false",
        MEMORY_DAEMON_AUTOSTART="false",
    )
    return subprocess.run(
        [sys.executable, "-c", _RUNNER, str(target), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=target,
        check=False,
    )


def _subtree(root: Path) -> dict[str, str]:
    """Exact state of everything under ``root`` (links by target, files by hash, dirs), without following links."""
    state: dict[str, str] = {}
    if not root.exists() and not root.is_symlink():
        return state
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                state[rel] = "link:" + os.readlink(path)
            elif path.is_dir():
                state[rel] = "dir"
            else:
                state[rel] = "file:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return state


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("worktrees-symlink")
    yield root
    reap_daemons_under(root, wait=True, by_process=True)
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def base_project(workspace: Path) -> Path:
    home = workspace / "home"
    home.mkdir()
    project = workspace / "base"
    project.mkdir()
    _git(project, "init", "-q")
    assert _run(project, "init", home).returncode == 0
    for _ in range(2):
        assert _run(project, "real", home).returncode == 0
        _git(project, "add", "-A")
    _git(project, *_NO_HOOKS, "commit", "-qm", "base")
    return project


def _copy(workspace: Path, base: Path, name: str) -> Path:
    project = workspace / name
    shutil.copytree(base, project, symlinks=True)
    return project


def _dirty_user_file(project: Path) -> tuple[Path, bytes]:
    """A tracked file the update refreshes, edited by the user (uncommitted)."""
    hook = project / ".claude" / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8")
    (project / ".claude" / "agents" / "mine.md").write_text("my agent\n", encoding="utf-8")
    (project / "notes").mkdir(exist_ok=True)
    (project / "notes" / "user.md").write_text("user-owned\n", encoding="utf-8")
    return hook, hook.read_bytes()


def _user_bytes(project: Path) -> dict[str, list[str]]:
    """Whole-tree byte accounting narrowed to bytes TRW does not own (refreshed managed files legitimately change)."""
    kept: dict[str, list[str]] = {}
    for digest, names in snapshot_user_bytes(project).items():
        mine = [
            n
            for n in names
            if n.startswith((".claude/worktrees/", "notes/")) or n.endswith(("session-start.sh", "agents/mine.md"))
        ]
        if mine:
            kept[digest] = mine
    return kept


def _outside(workspace: Path, name: str) -> Path:
    out = workspace / name
    out.mkdir()
    (out / "sentinel.txt").write_text("outside\n", encoding="utf-8")
    return out


@pytest.mark.parametrize("dangling", [False, True], ids=["live-target", "dangling-target"])
def test_symlink_under_claude_worktrees_does_not_block_update(
    workspace: Path, base_project: Path, dangling: bool
) -> None:
    tag = "dangling" if dangling else "live"
    project = _copy(workspace, base_project, f"wt-link-{tag}")
    outside = _outside(workspace, f"outside-{tag}")
    target = workspace / f"missing-{tag}" if dangling else outside
    link = project / ".claude" / "worktrees" / "w" / "ln"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    hook, edited = _dirty_user_file(project)
    outside_before, worktrees_before = _subtree(outside), _subtree(project / ".claude" / "worktrees")
    user_bytes = _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-1000:]
    assert _subtree(outside) == outside_before
    assert _subtree(project / ".claude" / "worktrees") == worktrees_before
    assert link.is_symlink() and os.readlink(link) == str(target)
    assert hook.read_bytes() == edited  # the dirty user file is preserved as before
    assert_user_bytes_preserved(user_bytes, project)


def test_symlink_under_non_pruned_managed_path_still_refused(workspace: Path, base_project: Path) -> None:
    project = _copy(workspace, base_project, "wt-control")
    outside = _outside(workspace, "outside-control")
    hook, edited = _dirty_user_file(project)
    link = project / ".claude" / "hooks" / "planted"
    link.symlink_to(outside)
    outside_before = _subtree(outside)
    user_bytes = _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode != 0
    assert "symlink" in proc.stderr + proc.stdout
    assert _subtree(outside) == outside_before
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)


def test_dirty_regular_files_under_claude_worktrees_are_not_deleted(workspace: Path, base_project: Path) -> None:
    """The data-loss form of the bug: a dirty regular file there had no snapshot copy, so the restore unlinked it.

    Three kinds, each judged independently (one combined assert lists every casualty): (a) tracked then
    modified, (b) untracked in a subdirectory, (c) untracked loose directly under ``worktrees``.
    """
    project = _copy(workspace, base_project, "wt-regular")
    root = project / ".claude" / "worktrees"
    tracked = root / "wt1" / "work.txt"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("v1\n", encoding="utf-8")
    _git(project, "add", "-f", str(tracked))
    _git(project, *_NO_HOOKS, "commit", "-qm", "track a file under worktrees")
    expected = {
        tracked: b"v1 edited\n",
        root / "wt1" / "sub" / "new.txt": b"untracked in a subdir\n",
        root / "loose.txt": b"loose untracked\n",
    }
    (root / "wt1" / "sub").mkdir()
    for path, data in expected.items():
        path.write_bytes(data)
    _dirty_user_file(project)
    before = _subtree(root)
    user_bytes = _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-1000:]
    lost = {
        path.relative_to(project).as_posix(): ("deleted" if not path.exists() else "changed")
        for path, data in expected.items()
        if not path.exists() or path.read_bytes() != data
    }
    assert not lost, f"user files under .claude/worktrees lost: {lost}"
    assert _subtree(root) == before
    assert_user_bytes_preserved(user_bytes, project)


def test_nested_git_worktree_under_claude_worktrees_is_untouched(workspace: Path, base_project: Path) -> None:
    """Regression guard only: the outer repo reports a nested worktree as ONE entry (asserted below), so
    the old code skipped it too; the fix proof is the regular-file test above."""
    project = _copy(workspace, base_project, "wt-nested")
    nested = project / ".claude" / "worktrees" / "x"
    _git(project, "worktree", "add", "-q", "-b", "nested-x", str(nested))
    assert nested.joinpath(".git").is_file()
    (nested / "AGENTS.md").write_text("nested uncommitted\n", encoding="utf-8")
    (nested / "new-untracked.txt").write_text("untracked\n", encoding="utf-8")
    (nested / "ln").symlink_to(_outside(workspace, "outside-nested"))
    status = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        check=True,
    ).stdout
    assert [e for e in status.split(b"\0") if b"worktrees" in e] == [b"?? .claude/worktrees/x/"]
    hook, edited = _dirty_user_file(project)
    nested_before = _subtree(nested)
    user_bytes = _user_bytes(project)

    proc = _run(project, "real", workspace / "home")

    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-1000:]
    assert _subtree(nested) == nested_before
    assert hook.read_bytes() == edited
    assert_user_bytes_preserved(user_bytes, project)
