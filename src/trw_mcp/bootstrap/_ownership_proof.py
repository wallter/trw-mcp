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
from ._retired_artifacts import CLIENT_SKILL_ROOTS

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


def _shipped_digest(path: Path, root: Path) -> set[str]:
    """The sha256 of the bundled file ``skills/<skill>/<file>`` that *path* (under some ``.../skills/<skill>/``) is a
    copy of, or empty: bytes identical to what TRW ships are TRW's, whatever the manifest recorded for this client."""
    from ._utils import _DATA_DIR

    parts = path.relative_to(root).parts
    if "skills" not in parts[:-2]:
        return set()
    tail = parts[parts.index("skills") + 1 :]
    shipped = _DATA_DIR.joinpath("skills", *tail)
    try:
        if shipped.is_symlink() or not shipped.is_file():
            return set()
        return {hashlib.sha256(shipped.read_bytes()).hexdigest()}
    except OSError:  # trw-fail-silent-allow: an unreadable bundle file proves nothing; the file stays kept
        return set()


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
        # A skill directory goes as one unit (never SKILL.md without its companion), and a file whose bytes are the
        # ones TRW ships for that skill is TRW's even where the manifest has no record of this client's copy.
        whole = artifact.is_dir() and artifact.parent.name == "skills"
        outcome = retire_tree(
            artifact,
            root,
            lambda f: recorded_digests(f, hashes, root, exact=exact),
            whole=whole,
            shipped=lambda f: _shipped_digest(f, root),
        )
        if whole and outcome.kept_dirs and artifact.parent.relative_to(root).as_posix() in CLIENT_SKILL_ROOTS:
            # A client mirror kept whole is named by its ``retired_artifact_present`` notice (path, replacement, ``rm -r``):
            # a second line from here is the double report of a retired skill. It is still recorded as kept.
            result.setdefault("retired_kept", []).extend(p for p, _why in outcome.kept)
        else:
            record_retirement(result, outcome)
    if outcome.kept:
        result.setdefault("preserved", []).append(f"{rel} (not_installer_owned)")
        logger.info("sweep_removal_preserved", path=rel, reason="not_installer_owned")
