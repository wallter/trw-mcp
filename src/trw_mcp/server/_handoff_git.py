"""Read-only git queries behind ``trw-mcp handoff new`` and ``check``.

Belongs to the ``_subcommands_handoff.py`` verb shell. Kept out of :mod:`trw_mcp.handoff`,
which must import no subprocess module (``test_ahr_security``); the results travel into the
package as a plain :class:`~trw_mcp.handoff._repo.GitState`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

from trw_mcp.handoff._repo import UNKNOWN_GIT, GitState

__all__ = ["commits_since", "git_state", "repo_root"]

_TIMEOUT_S = 10.0
# Variables that would point git at another repository than the one asked about (a hook's GIT_DIR).
_REPO_ENV = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"})


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one read-only git query; ``OSError`` when git is missing or hangs."""
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV} | {"GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            # No fsmonitor daemon or untracked cache: a repo's config must not run a hook or serve stale state.
            ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-C", str(root), *args],  # noqa: S607
            env=env,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"git {args[0]} timed out after {_TIMEOUT_S:.0f}s") from exc


def repo_root(start: Path) -> Path | None:
    """The git top-level containing ``start``, or ``None`` when it is not in a work tree."""
    try:
        done = _git(start, "rev-parse", "--show-toplevel")
    except OSError:  # trw-fail-silent-allow: no usable git means "not a repo"; callers label the state unknown
        return None
    top = done.stdout.strip()
    return Path(top).resolve() if done.returncode == 0 and top else None


def _own_file(path: str, skip_dir: str | None, handoff_id: str | None) -> bool:
    """A file of the record itself: ``<id>.json``, ``.md``, ``.changed-paths.txt`` or a read-back, beside it."""
    if skip_dir is None or not handoff_id:
        return False
    pure = PurePosixPath(path)
    return pure.parent.as_posix() == skip_dir and pure.name.startswith(f"{handoff_id}.")


def _porcelain(root: Path, exclude_dir: Path | None, handoff_id: str | None) -> tuple[str, ...]:
    done = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if done.returncode != 0:
        raise OSError(f"git status failed: {done.stderr.strip()[:200]}")
    skip = None
    if exclude_dir is not None and exclude_dir.resolve().is_relative_to(root):
        skip = exclude_dir.resolve().relative_to(root).as_posix()
    paths: list[str] = []
    entries = iter(done.stdout.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            next(entries, None)  # -z puts the rename/copy source in its own field
        if not _own_file(path, skip, handoff_id):
            paths.append(path)  # the path only: the XY status code is not part of the comparison
    return tuple(paths)


def git_state(root: Path | None, *, exclude_dir: Path | None = None, handoff_id: str | None = None) -> GitState:
    """HEAD commit and clean/dirty state of ``root``; the record's own files (by ``handoff_id``) do not count."""
    if root is None:
        return UNKNOWN_GIT
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    commit = head.stdout.strip() if head.returncode == 0 else None  # an unborn branch has no HEAD yet
    changed = _porcelain(root, exclude_dir, handoff_id)
    return GitState(commit, "dirty" if changed else "clean", changed)


def commits_since(root: Path, commit: str) -> int | None:
    """Commits on HEAD after ``commit``; ``None`` when ``commit`` is unknown or not an ancestor of HEAD."""
    if _git(root, "merge-base", "--is-ancestor", commit, "HEAD").returncode != 0:
        return None
    done = _git(root, "rev-list", "--count", f"{commit}..HEAD")
    return int(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip().isdigit() else None
