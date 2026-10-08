"""Resolve repository identity and shared cache paths; refuse unsafe cache storage."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Literal

#: Every git probe of one sidecar lookup draws on ONE budget: a probe gets what is left of it, never
#: a fixed slice, so a loaded host is not misread as "missing" after a quarter second.
PROBE_BUDGET_S = 1.0


def probe_timeout(deadline: float | None) -> float:
    """Seconds a git probe may take now: the rest of the lookup's budget, at most ``PROBE_BUDGET_S``.

    A spent budget raises ``subprocess.TimeoutExpired`` before anything is started: every probe
    already treats that as "git did not answer in time", so an expired deadline runs no probe at all.
    """
    left = PROBE_BUDGET_S if deadline is None else deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired("git", 0.0)
    return min(PROBE_BUDGET_S, left)


def resolve_repo_root(repo_root: str | None, deadline: float | None = None) -> Path | None:
    """Best-effort repo-root resolution (caller arg → git rev-parse)."""
    if repo_root is not None:
        return Path(repo_root)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=probe_timeout(deadline),
            check=False,
        )
        if proc.returncode == 0:
            stripped = proc.stdout.strip()
            if stripped:
                return Path(stripped)
    # trw-fail-silent-allow: git missing or slow means "no repository root"; the caller falls back and says so
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def shared_cache_dir(repo_root: Path, cache_rel: str) -> Path:
    """*cache_rel* under the main checkout for every linked worktree, regardless of local cache directories.

    Resolved from the ``.git`` file and ``commondir`` alone (no subprocess), so
    every worktree of a repository reads the one cache the main checkout owns.
    Falls back to ``repo_root / cache_rel`` whenever that layout does not hold.
    """
    local = repo_root / cache_rel
    dotgit = repo_root / ".git"
    if not dotgit.is_file():
        return local
    try:
        text = dotgit.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return local
        gitdir = Path(text.removeprefix("gitdir:").strip())
        gitdir = gitdir if gitdir.is_absolute() else repo_root / gitdir
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return local
    return common.parent / cache_rel if common.name == ".git" else local


def resolve_git_sha(repo_root: Path, deadline: float | None = None) -> str | None:
    """Best-effort ``git rev-parse HEAD`` with validation (40-char hex)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=probe_timeout(deadline),
            check=False,
        )
        if proc.returncode != 0:
            return None
        stripped = proc.stdout.strip()
        if len(stripped) == 40 and all(c in "0123456789abcdef" for c in stripped):
            return stripped
    # trw-fail-silent-allow: git missing or slow means "no commit id"; the caller reports the hint as unavailable
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def cache_is_safe(cache_dir: Path, repo_root: Path) -> bool:
    """True only for a cache shown to be unlinked and untracked; unknown fails closed."""
    return cache_safety(cache_dir, repo_root) == "safe"


def cache_safety(
    cache_dir: Path, repo_root: Path, deadline: float | None = None
) -> Literal["safe", "unsafe", "unknown"]:
    """Refuse symlinked cache components/files and git-tracked cache contents.

    Check the cache owner's index (also for a linked worktree) within the lookup's
    probe budget. ``unknown`` is a git probe that could not answer in time: the
    cache is not trusted then, and it is not reported as missing either.
    """
    cache_dir = cache_dir.absolute()
    owner = next((p for p in cache_dir.parents if (p / ".git").exists()), repo_root).absolute()
    # Only what the checkout controls is judged: the cache directory and each component below the
    # owning checkout. A symlink ABOVE it (macOS /var and /tmp, a home on another volume) is the
    # user's own filesystem layout, and refusing it would refuse every sidecar on such a machine.
    below = [path for path in (cache_dir, *cache_dir.parents) if owner in path.parents]
    if any(path.is_symlink() for path in below):
        return "unsafe"
    if any(path.is_symlink() for path in cache_dir.glob("*.json")):
        return "unsafe"
    try:
        relative = cache_dir.relative_to(owner).as_posix()
        proc = subprocess.run(  # noqa: S603 - fixed git argv and literal pathspec after --
            ["git", "--literal-pathspecs", "ls-files", "-z", "--", relative],  # noqa: S607
            cwd=owner,
            capture_output=True,
            timeout=probe_timeout(deadline),
            check=False,
        )
        return "safe" if proc.returncode == 0 and not proc.stdout else "unsafe"
    # trw-fail-silent-allow: a probe that timed out is reported as unknown, never as a safe cache
    except subprocess.TimeoutExpired:
        return "unknown"
    # trw-fail-silent-allow: fail closed; a cache that cannot be shown untracked and unlinked is refused
    except (ValueError, OSError):
        return "unsafe"
