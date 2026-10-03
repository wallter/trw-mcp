"""A stable, portable id for the project a learning was written in (SYNC-PROJECT-IDENTITY).

Pushed learnings carry it so a host that pulls them can tell its own project's knowledge from the operator's other
projects. ``git:`` plus the first 16 hex of sha256 over the repository's root commit id (the smallest over all refs when
there are several): the same in every clone on every machine, and it reveals no name, path or URL. Where git cannot say
(no repository, git missing, or a shallow clone whose "root" is only its cut-off) it falls back to ``ns:`` plus the
same hash of the project namespace, which is stable for one checkout and not across machines.

A submodule checkout resolves its own repository, so it has the submodule's identity, not its parent's.

Computed once per project per process; a file cache inside ``.trw`` would carry a stale id into any copy of it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

__all__ = ["own_project_ids", "project_id", "reset_cache"]

_GIT_TIMEOUT_SECONDS = 5.0
_cache: dict[Path, tuple[str, frozenset[str]]] = {}


def reset_cache() -> None:
    """Forget every computed id (tests)."""
    _cache.clear()


def _git(root: Path, *args: str) -> str | None:
    """git's stdout, or ``None`` when it cannot answer. The child gets a minimal environment, not ours."""
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "SYSTEMROOT") if key in os.environ}
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, the argument is a path we resolved
            ["git", "-C", str(root), *args],  # noqa: S607
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: no git means the ns: id, by design
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _roots(root: Path) -> list[str]:
    """Every root commit id of the repository at *root*, smallest first; ``[]`` unless git can name the real ones.

    Taken over ALL refs, not HEAD: a docs or pages orphan branch is a second root, and which branch is checked out
    must not change a machine's id.
    """
    if _git(root, "rev-parse", "--is-shallow-repository") != "false":
        return []  # a shallow clone reports its cut-off commit as a root
    return sorted((_git(root, "rev-list", "--max-parents=0", "--all") or "").split())


def _short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _ids(project_root: Path, namespace: str) -> tuple[str, frozenset[str]]:
    key = project_root.resolve()
    if key not in _cache:
        roots = _roots(key)
        if roots:
            _cache[key] = (f"git:{_short(roots[0])}", frozenset(f"git:{_short(commit)}" for commit in roots))
        else:
            fallback = f"ns:{_short(namespace)}"
            _cache[key] = (fallback, frozenset({fallback}))
    return _cache[key]


def project_id(project_root: Path, *, namespace: str) -> str:
    """The id to stamp: from the smallest root commit; *namespace* is the fallback when git cannot name one."""
    return _ids(project_root, namespace)[0]


def own_project_ids(project_root: Path, *, namespace: str) -> frozenset[str]:
    """Every id that means this project: one per root commit, so a row stamped before another history was merged in
    (a root that is no longer the smallest) is still this project's."""
    return _ids(project_root, namespace)[1]
