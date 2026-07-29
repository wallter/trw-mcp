"""Revision-case handling for the commit-range checks (PRD-SEC-013 R9).

Every commit in an outgoing range is classified explicitly:

* root commits compare against the empty tree (``base is None``);
* merge commits produce ONE pair per parent — a claim weakened relative to ANY
  parent is weakened for the merge, so a merge cannot launder a one-sided
  weakening through the other parent's clean state;
* zero-SHA new refs enumerate commits not reachable from any other ref, which
  is merge-base semantics when the new branch shares history and empty-tree
  semantics when it does not;
* force-push ranges use ``merge-base(old, new)`` and FAIL CLOSED when *old* is
  unreachable (pruned/rewritten history — the true baseline cannot be
  established).

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trw_mcp.security.intent_contract._git_run import GitCommandError, run_git

__all__ = [
    "ZERO_SHA",
    "RangeResolutionError",
    "RevisionPair",
    "commit_pairs",
    "enumerate_revision_pairs",
]

ZERO_SHA = "0" * 40


class RangeResolutionError(Exception):
    """The true baseline for a range cannot be established — always fail closed."""


@dataclass(frozen=True)
class RevisionPair:
    """One ``(base, candidate)`` comparison. ``base is None`` means the empty tree."""

    candidate: str
    base: str | None


def _is_zero(sha: str | None) -> bool:
    return not sha or set(sha) == {"0"}


def _parents(repo_root: Path, sha: str) -> list[str]:
    line = run_git(repo_root, "rev-list", "--parents", "-n", "1", sha).strip()
    return line.split()[1:]


def commit_pairs(repo_root: Path, sha: str) -> list[RevisionPair]:
    """Every ``(parent, sha)`` pair for one commit; empty-tree base for a root commit."""
    parents = _parents(repo_root, sha)
    if not parents:
        return [RevisionPair(candidate=sha, base=None)]
    return [RevisionPair(candidate=sha, base=parent) for parent in parents]


def _other_refs(repo_root: Path, new_sha: str) -> list[str]:
    """Ref names that do NOT already point at *new_sha* (i.e. the pushed ref itself)."""
    try:
        raw = run_git(repo_root, "for-each-ref", "--format=%(objectname) %(refname)")
    except GitCommandError:
        return []
    refs: list[str] = []
    for line in raw.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0] != new_sha:
            refs.append(parts[1])
    return refs


def _range_commits(repo_root: Path, old_sha: str | None, new_sha: str) -> list[str]:
    if old_sha is None or _is_zero(old_sha):
        # New ref: everything not already reachable from a DIFFERENT ref. That is
        # merge-base semantics when the branch shares history, and empty-tree
        # semantics (back to the root commit) when it does not.
        others = _other_refs(repo_root, new_sha)
        if others:
            return run_git(repo_root, "rev-list", "--reverse", new_sha, "--not", *others).split()
        return run_git(repo_root, "rev-list", "--reverse", new_sha).split()
    try:
        run_git(repo_root, "cat-file", "-e", f"{old_sha}^{{commit}}")
    except GitCommandError as exc:
        raise RangeResolutionError(f"base {old_sha} is unreachable (pruned or rewritten history)") from exc
    try:
        base = run_git(repo_root, "merge-base", old_sha, new_sha).strip()
    except GitCommandError as exc:
        raise RangeResolutionError(f"no merge-base between {old_sha} and {new_sha}") from exc
    if not base:
        raise RangeResolutionError(f"empty merge-base between {old_sha} and {new_sha}")
    return run_git(repo_root, "rev-list", "--reverse", f"{base}..{new_sha}").split()


def enumerate_revision_pairs(repo_root: Path, old_sha: str | None, new_sha: str) -> list[RevisionPair]:
    """Every ``(base, candidate)`` pair in the outgoing range, one per parent.

    Raises :class:`RangeResolutionError` when the baseline is unresolvable, which
    every caller maps to a fail-closed rejection rather than an empty result.
    """
    try:
        commits = _range_commits(repo_root, old_sha, new_sha)
    except GitCommandError as exc:
        raise RangeResolutionError(f"range walk failed: {exc}") from exc
    pairs: list[RevisionPair] = []
    for sha in commits:
        try:
            pairs.extend(commit_pairs(repo_root, sha))
        except GitCommandError as exc:
            raise RangeResolutionError(f"parent lookup failed for {sha}: {exc}") from exc
    return pairs
