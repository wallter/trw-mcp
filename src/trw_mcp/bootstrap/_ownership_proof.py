"""Proof that TRW wrote an artifact, and the guarded removal that re-proves it at the act.

Every retirement sweep (``_version_migration._remove_stale_artifacts`` and
``_version_migration_clients._remove_stale_client_surface``) decides through these four functions: an artifact
is removed only when every regular file under it hashes to its record in the PRE-run manifest's
``content_hashes`` (PRD-FIX-139-FR01, PRD-INFRA-190-FR06), and the removal captures each file into
``.trw/trash`` and re-verifies it there (``remove_tree_if_hash``). Anything unproven is kept and reported
``not_installer_owned``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import structlog

from ._safe_remove import remove_tree_if_hash

logger = structlog.get_logger(__name__)

__all__ = ["_trw_authored", "preserve_unowned", "recorded_digests", "remove_proven"]


def preserve_unowned(
    artifact: Path,
    manifest_hashes: dict[str, str] | None,
    target_dir: Path,
    result: dict[str, list[str]],
    *,
    exact: bool = False,
) -> bool:
    """Report and keep *artifact* when TRW cannot prove it wrote it (FR06); True means keep.

    *exact* restricts the proof to the file's own repo-relative key (see :func:`recorded_digests`).
    """
    if _trw_authored(artifact, manifest_hashes or {}, target_dir, exact=exact):
        return False
    rel = artifact.relative_to(target_dir).as_posix()
    result.setdefault("preserved", []).append(f"{rel} (not_installer_owned)")
    logger.info("sweep_removal_preserved", path=rel, reason="not_installer_owned")
    return True


def _trw_authored(
    artifact: Path, manifest_hashes: dict[str, str], target_dir: Path | None, *, exact: bool = False
) -> bool:
    """True when every regular file under *artifact* hashes to its manifest record.

    Manifest keys are written in more than one shape (``<skill>/SKILL.md`` for
    ``.claude/skills``, ``.opencode/skills/<skill>/SKILL.md`` for opencode, bare
    ``<agent>.md`` for ``.claude/agents``), so a file is matched by any recorded
    key that equals its path relative to *target_dir* or a suffix of it at a
    path boundary. An unreadable file counts as unproven.
    """
    files = [f for f in sorted(artifact.rglob("*")) if f.is_file()] if artifact.is_dir() else [artifact]
    if not files:
        return False
    for path in files:
        recorded = recorded_digests(path, manifest_hashes, target_dir, exact=exact)
        if not recorded:
            return False
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() not in recorded:
                return False
        except OSError:  # trw-fail-silent-allow: unreadable means unproven; False preserves the artifact (the safe direction) and the warning above-the-fold reports it
            logger.warning("predecessor_authorship_unreadable", path=str(path), exc_info=True)
            return False
    return True


def recorded_digests(
    path: Path, manifest_hashes: dict[str, str], target_dir: Path | None, *, exact: bool = False
) -> set[str]:
    """The manifest digests that prove *path* is TRW's (see :func:`_trw_authored` for the key shapes).

    *exact*: only the file's own repo-relative key counts. Client agent and command surfaces pass it,
    because their recorders write exact keys, so a suffix match there can only be a record for a
    different file that shares the name (CLIENT-SURFACE-SUFFIX-PROOF).
    """
    rel = path.relative_to(target_dir).as_posix() if target_dir is not None else path.as_posix()
    # The exact manifest key alone decides. Suffix records (``x/SKILL.md`` is a
    # suffix of ``.agents/skills/x/SKILL.md`` but holds the claude-code bytes)
    # apply only when no exact key exists, so a suffix digest can never
    # authorize deleting a file whose own record says it was modified.
    if rel in manifest_hashes:
        return {manifest_hashes[rel]}
    if exact:
        return set()
    return {digest for key, digest in manifest_hashes.items() if rel.endswith("/" + key)}


def remove_proven(
    artifact: Path,
    manifest_hashes: dict[str, str] | None,
    root: Path,
    result: dict[str, list[str]],
    *,
    exact: bool = False,
) -> None:
    """Remove a proven-stale *artifact* via :func:`remove_tree_if_hash`, re-proving every file at the act.

    The proof ``preserve_unowned`` took earlier is not trusted at delete time: each file is re-hashed and
    captured into ``.trw/trash``, so an edit saved in between (or a file added) keeps its bytes.
    """
    hashes = manifest_hashes or {}
    before = {f for f in artifact.rglob("*") if f.is_file() and not f.is_symlink()} if artifact.is_dir() else {artifact}
    captured: dict[Path, Path] = {}
    kept = remove_tree_if_hash(
        artifact, root, lambda f: recorded_digests(f, hashes, root, exact=exact), captured=captured
    )
    # Every captured file is reported as trashed: the uncommitted-changes guard restores a dirty path it did not
    # see captured (so a retired skill came back in the client mirrors the user had touched), and the CLI names
    # only what is listed here (the nine silent skill files in feedback sub_i7UMmxUbTbsdW0eD, FB-INSTALL-03).
    gone = [f for f in sorted(before) if not os.path.lexists(f)]
    result.setdefault("trashed", []).extend(f.relative_to(root).as_posix() for f in gone)
    # "<rel>\t<capture folder>": the CLI line names the folder that holds the bytes (S8a).
    result.setdefault("trash_captures", []).extend(
        f"{f.relative_to(root).as_posix()}\t{captured[f].relative_to(root).as_posix()}" for f in gone if f in captured
    )
    # Warnings are what the CLI prints (preserved is summarised as a count); a kept file must say why.
    result.setdefault("warnings", []).extend(f"{why}: kept" for why in kept)
