"""Proof that TRW wrote an artifact, and the in-place retirement that acts on it.

Every retirement sweep (``_version_migration._remove_stale_artifacts`` and
``_version_migration_clients._remove_stale_client_surface``) decides through these functions: a file is removed
when it hashes to its record in the PRE-run manifest's ``content_hashes`` (PRD-FIX-139-FR01,
PRD-INFRA-190-FR06) or is clean in git (``_retire``). Anything else is kept, reported ``not_installer_owned``
and named with the command that removes it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from ._retire import Retirement, record_retirement, retire_tree

logger = structlog.get_logger(__name__)

__all__ = ["_trw_authored", "recorded_digests", "remove_proven"]


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
    """Retire a stale *artifact* in place (see :func:`retire_tree`): each file hashing to its manifest record, or
    clean in git, is deleted and the rest is kept, named with the command that removes it (FR06)."""
    hashes = manifest_hashes or {}
    rel = artifact.relative_to(root).as_posix()
    if artifact.is_dir() and not any(f.is_file() for f in artifact.rglob("*")):
        outcome = Retirement([], [], [(rel, "nothing in it is recorded as TRW's")])  # an empty dir proves nothing
    else:
        outcome = retire_tree(artifact, root, lambda f: recorded_digests(f, hashes, root, exact=exact))
        record_retirement(result, outcome)
    if outcome.kept:
        result.setdefault("preserved", []).append(f"{rel} (not_installer_owned)")
        logger.info("sweep_removal_preserved", path=rel, reason="not_installer_owned")
