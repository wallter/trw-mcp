"""Worktree-preserving Git merge primitive for the formation queue (CORE-296 FR07).

Only a compare-and-swap ref update mutates an existing object. Conflict
preflight and the merge commit are unreachable objects until that final CAS.
No checkout, index, worktree, reset, stash, rebase or force operation occurs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from trw_mcp.formation._manifest import FormationError

_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_VERSION = re.compile(r"git version (\d+)\.(\d+)")


class MergeGitError(FormationError):
    """A closed, recoverable Git stop with a stable reason."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed Git executable, argv only, no shell
            ["git", "-C", str(repo), *args],  # noqa: S607 - Git is resolved from PATH by design
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeGitError("git_unavailable", f"Git operation unavailable: {type(exc).__name__}") from exc
    if out.returncode:
        raise MergeGitError("git_refused", f"git {args[0]} refused: {out.stderr.strip()[:300]}")
    return out.stdout


def _version(repo: Path) -> None:
    match = _VERSION.search(_git(repo, "--version"))
    if match is None or (int(match[1]), int(match[2])) < (2, 38):
        raise MergeGitError("git_too_old", "merge-tree --write-tree requires Git 2.38 or newer")


def _branch_ref(repo: Path, branch: str) -> str:
    if not branch or branch.startswith(("-", "refs/")):
        raise MergeGitError("invalid_branch", "name a local branch, not a ref or option")
    _git(repo, "check-ref-format", "--branch", branch)
    return f"refs/heads/{branch}"


def _sha(repo: Path, ref: str) -> str:
    value = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if _SHA.fullmatch(value) is None:
        raise MergeGitError("invalid_sha", "Git returned a malformed commit id")
    return value


def _paths(repo: Path, before: str, after: str) -> tuple[str, ...]:
    # With rename detection off, both sides of a rename are checked for ownership.
    raw = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", before, after)
    return tuple(path for path in raw.split("\x00") if path)


def _target_not_checked_out(repo: Path, target_ref: str) -> None:
    if any(line == f"branch {target_ref}" for line in _git(repo, "worktree", "list", "--porcelain").splitlines()):
        raise MergeGitError(
            "target_checked_out", "target branch is checked out; use an un-checked-out integration branch"
        )


def _protected_target(repo: Path, target: str) -> None:
    if target in {"main", "master"}:
        raise MergeGitError(
            "protected_target", "the queue never targets main or master; lead fast-forwards main separately"
        )
    try:
        origin_head = subprocess.run(  # noqa: S603 - fixed read-only Git argv, no shell
            ["git", "-C", str(repo), "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],  # noqa: S607 - Git from PATH
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergeGitError("git_unavailable", "cannot check the remote default branch") from exc
    if origin_head.returncode not in {0, 1}:
        raise MergeGitError("default_branch_unavailable", "cannot verify origin's default branch")
    if origin_head.returncode == 0 and origin_head.stdout.strip() == f"origin/{target}":
        raise MergeGitError("protected_target", "the queue never targets origin's default branch")


def prepare(repo: Path, branch: str, sha: str, target: str) -> tuple[str, str, tuple[str, ...]]:
    """Preflight an immutable branch and merge, returning old target, tree and paths."""
    _version(repo)
    if _SHA.fullmatch(sha) is None:
        raise MergeGitError("invalid_sha", "approved SHA is malformed")
    branch_ref, target_ref = _branch_ref(repo, branch), _branch_ref(repo, target)
    _protected_target(repo, target)
    _target_not_checked_out(repo, target_ref)
    if _sha(repo, branch_ref) != sha:
        raise MergeGitError("stale_sha", "approved branch tip no longer equals approved SHA")
    old = _sha(repo, target_ref)
    base = _git(repo, "merge-base", old, sha).strip()
    changed = _paths(repo, base, sha)
    try:
        tree = _git(repo, "merge-tree", "--write-tree", old, sha).splitlines()[0]
    except MergeGitError as exc:
        raise MergeGitError("conflict", "merge-tree refused or found a conflict; target is unchanged") from exc
    if _SHA.fullmatch(tree) is None:
        raise MergeGitError("conflict", "merge-tree returned no clean tree; target is unchanged")
    return old, tree, changed


def apply(repo: Path, branch: str, sha: str, target: str, old: str, tree: str) -> str:
    """Commit a preflighted tree and CAS-update only an un-checked-out target ref."""
    branch_ref, target_ref = _branch_ref(repo, branch), _branch_ref(repo, target)
    _protected_target(repo, target)
    _target_not_checked_out(repo, target_ref)
    if _sha(repo, branch_ref) != sha:
        raise MergeGitError("stale_sha", "approved branch moved after preflight")
    if _sha(repo, target_ref) != old:
        raise MergeGitError("target_moved", "integration branch moved after preflight")
    merged = _git(repo, "commit-tree", tree, "-p", old, "-p", sha, "-m", f"Merge approved {branch} at {sha}").strip()
    if _SHA.fullmatch(merged) is None:
        raise MergeGitError("git_refused", "commit-tree returned no commit id")
    try:
        _git(repo, "update-ref", target_ref, merged, old)
    except MergeGitError as exc:
        raise MergeGitError("target_moved", "integration branch CAS failed; no retry without a new approval") from exc
    return merged
