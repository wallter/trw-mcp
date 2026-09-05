"""Anchor matching + the enforcement-eligibility filter shared by FR02/FR05/FR07.

The filter lives here exactly once so the pre-write hook, the post-edit check and
the git-side detector cannot drift apart about which claims are enforceable.
NOTE (R7): this filter is NEVER applied before the weaken predicate — filtering
first is the bypass codex #1 describes.

Path matching alone is not sufficient identity. A HARDLINK alias is a second name
for the same inode, and a SYMLINK alias is a second name for the same file, so an
edit through either changes the anchored file while its own path matches no
anchor. When (and only when) the target is multi-linked OR is a symlink, matching
falls back to ``(st_dev, st_ino)`` identity against the anchored files — the
common plain-file case still pays no extra ``stat`` calls.

Two shapes of that fallback were probe-verified as bypasses on 2026-07-24 and are
closed here:

* N1 — a symlink alias ``lstat``\\ s with ``st_nlink == 1``, so the fallback never
  ran at all. Aliasing is now detected from the link TYPE as well as the count,
  and identity follows the final component.
* N2 — a hardlink out of a DIRECTORY-anchored tree compared the file's inode
  against the DIRECTORY's, which can never match. Directory anchors are now
  expanded to their file set (bounded) before identity comparison.

Following the final symlink here is safe and deliberate: escape from the repo root
is classified earlier by ``classify_target``, and the post-edit READ still uses
``O_NOFOLLOW``. Following only ever widens what counts as protected.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import stat
from fnmatch import fnmatchcase
from pathlib import Path

from trw_mcp.security.intent_contract._models import Contract, MustNotHappenClaim

__all__ = ["anchor_matches", "claims_matching_path", "eligible_claims", "enforceable_claims", "is_glob_anchor"]

_GLOB_CHARS = frozenset("*?[")

#: Bound on glob expansion per anchor so a pathological anchor cannot blow the
#: hook latency budget while resolving inode identity.
_MAX_GLOB_MATCHES = 256


def is_glob_anchor(anchor: str) -> bool:
    """True when *anchor* carries a glob metacharacter rather than naming a path.

    The one place the ``*?[`` set is interpreted. :func:`anchor_matches` and the
    FR01 sidecar renderer must agree about which anchors are globs — a renderer
    that classified one of them differently would emit a pattern the Python
    matcher does not use, and the shell fast path would then answer a question
    Python never asked.
    """
    return bool(_GLOB_CHARS & set(anchor))


def eligible_claims(contract: Contract) -> tuple[MustNotHappenClaim, ...]:
    """The enforcement-eligibility filter, exactly once (FR02/FR05/FR07 + FR01).

    Extracted from :func:`enforceable_claims` so the PRD-CORE-254 sidecar renders
    the SAME claim set the hooks enforce. A sidecar built from a wider filter
    would be harmless (extra deferrals); one built from a narrower filter would
    let the shell self-approve a write a claim covers, which is the one direction
    the fast path may never take.
    """
    return tuple(
        claim
        for claim in contract.claims
        if claim.binding_channel == "blocking_hook" and claim.machine_checkable and claim.state == "active"
    )


def anchor_matches(anchor: str, rel_path: str) -> bool:
    """True when *rel_path* (repo-relative, posix) is covered by *anchor*.

    A glob-free anchor matches the exact path or anything beneath it when it
    names a directory; a glob anchor is matched with ``fnmatch`` semantics.
    """
    if not anchor or not rel_path:
        return False
    normalized = anchor.rstrip("/")
    if is_glob_anchor(normalized):
        return fnmatchcase(rel_path, normalized)
    return rel_path == normalized or rel_path.startswith(f"{normalized}/")


def claims_matching_path(claims: tuple[MustNotHappenClaim, ...], rel_path: str) -> tuple[MustNotHappenClaim, ...]:
    """Claims whose ``anchors[]`` cover *rel_path* (no eligibility filtering)."""
    return tuple(claim for claim in claims if any(anchor_matches(a, rel_path) for a in claim.anchors))


def _identity(path: Path) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` of the file *path* names, FOLLOWING a final symlink."""
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _may_be_aliased(path: Path) -> bool:
    """True when *path* may be a second name for some other file.

    Both aliasing shapes count. Checking ``st_nlink > 1`` alone missed symlinks
    entirely — a symlink has exactly one link of its own — so a symlink alias to a
    protected file never reached identity matching (probe finding N1).
    """
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or info.st_nlink > 1


def _anchor_paths(root: Path, anchor: str) -> list[Path]:
    normalized = anchor.rstrip("/")
    if not normalized:
        return []
    if is_glob_anchor(normalized):
        matches: list[Path] = []
        try:
            for index, match in enumerate(root.glob(normalized)):
                if index >= _MAX_GLOB_MATCHES:
                    break
                matches.append(match)
        except (OSError, ValueError):
            return []
        return matches
    return [root / normalized]


def _anchor_files(root: Path, anchor: str) -> list[Path]:
    """Every FILE an anchor covers — walking a DIRECTORY anchor into its members.

    ``_anchor_paths`` yields the anchor itself, and a directory's inode can never
    equal a file's, so a hardlink out of a directory-anchored tree matched nothing
    (probe finding N2) even though a direct path under the same anchor matched.
    Expansion stays bounded by ``_MAX_GLOB_MATCHES`` so a repo-root anchor cannot
    blow the hook latency budget; this runs only on the rare alias path.
    """
    files: list[Path] = []
    for candidate in _anchor_paths(root, anchor):
        if len(files) >= _MAX_GLOB_MATCHES:
            break
        try:
            walkable = candidate.is_dir() and not candidate.is_symlink()
        except OSError:
            continue
        if not walkable:
            files.append(candidate)
            continue
        try:
            for member in candidate.rglob("*"):
                if len(files) >= _MAX_GLOB_MATCHES:
                    break
                files.append(member)
        except (OSError, ValueError):
            continue
    return files


def _claims_matching_inode(
    claims: tuple[MustNotHappenClaim, ...], root: Path, target: Path
) -> tuple[MustNotHappenClaim, ...]:
    identity = _identity(target)
    if identity is None:
        return ()
    matched: list[MustNotHappenClaim] = []
    for claim in claims:
        for anchor in claim.anchors:
            if any(_identity(candidate) == identity for candidate in _anchor_files(root, anchor)):
                matched.append(claim)
                break
    return tuple(matched)


def enforceable_claims(
    contract: Contract,
    rel_path: str,
    root: Path | None = None,
    target: Path | None = None,
) -> tuple[MustNotHappenClaim, ...]:
    """Active, machine-checkable, blocking-hook claims governing this write.

    *root* and *target* enable hardlink- AND symlink-alias detection; without them
    the match is path-only (that is the git-side callers' case — they have no
    working tree to stat).
    """
    eligible = eligible_claims(contract)
    matched = claims_matching_path(eligible, rel_path)
    if matched or root is None or target is None or not _may_be_aliased(target):
        return matched
    return _claims_matching_inode(eligible, root, target)
