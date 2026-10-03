"""Pre-write backup and retention for instruction files (PRD-FIX-123-FR04).

Belongs to the ``_write_guard.py`` seam; nothing else calls it. Split out so the
guard stays a single readable decision procedure and this stays a single
readable filesystem procedure, both under the module-size gate.

Posture is fail-CLOSED and the caller depends on it: every function here raises
on failure so the guard REFUSES the write. A degraded instruction file is
recoverable; destroyed user content is not (PRD-FIX-123-NFR02).
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts._ceremony import InstructionRefusalReason

logger = structlog.get_logger(__name__)

#: Timestamp suffix appended to a backup copy: basic-format UTC with
#: microseconds, so two writes inside one second cannot collide and lexical
#: sort order equals chronological order (which is what retention prunes on).
_BACKUP_STAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


class BackupRefused(Exception):
    """A backup could not be taken, so the guarded write must be refused."""

    def __init__(self, reason: InstructionRefusalReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def resolve_backup_dir(project_root: Path, backup_dir: str) -> Path:
    """Resolve *backup_dir* under *project_root*, refusing any escape.

    ``instruction_backup_dir`` is operator-overridable, so a value like
    ``../../tmp/x`` would otherwise let TRW write outside the project. An
    escaping backup directory REFUSES the write, because the backup is the only
    thing standing between a bad candidate and unrecoverable user content.

    Raises:
        BackupRefused: when the resolved directory escapes *project_root*.
    """
    candidate = project_root / backup_dir
    # ``resolve()`` normalizes ``..`` so containment holds for not-yet-existing paths.
    if not candidate.resolve().is_relative_to(project_root.resolve()):
        raise BackupRefused(
            "backup_path_escape",
            f"instruction_backup_dir {backup_dir!r} resolves outside the project root {project_root}",
        )
    return candidate


def _regular_copies(backup_dir: Path, filename: str) -> list[Path]:
    """The retained copies of *filename*, oldest first: REGULAR files only, judged by ``lstat``.

    A symlink (even one to the live instruction file) or a directory is never a copy: it is neither
    reused as the newest backup nor pruned. A bounded ``glob`` over one filename's siblings, never a
    recursive walk (PRD-FIX-123-NFR01).
    """
    copies = []
    for path in backup_dir.glob(f"{filename}.*"):
        try:
            if stat.S_ISREG(path.lstat().st_mode):
                copies.append(path)
        except OSError:  # trw-fail-silent-allow: a sibling that vanished mid-scan is not a copy
            continue
    return sorted(copies)


#: ``<name>.<stamp>.<sha256 prefix>``: a backup that a write displaced carries its content hash in its name.
_HASHED = re.compile(r"\.\d{8}T\d{12}Z\.([0-9a-f]{16})$")


def _still_as_taken(copy: Path) -> bool:
    """True when *copy* may be pruned: its bytes still match the content hash in its name.

    A displaced backup is the user's former file, so a program that kept it open can still write to it; such a
    copy is kept past retention instead of deleted (PUBLISH-RACE-HARDEN codex r1). A legacy copy without a hash
    in its name cannot be proven unchanged, so it is kept too (CLAUDE-MD S1 red team).
    """
    match = _HASHED.search(copy.name)
    if match is None:
        return False
    try:
        return hashlib.sha256(copy.read_bytes()).hexdigest()[:16] == match.group(1)
    except OSError:  # trw-fail-silent-allow: unreadable means unproven, so it is kept
        return False


def _prune_retention(backup_dir: Path, filename: str, retention: int) -> None:
    """Keep at most *retention* copies of *filename*, oldest pruned first, each only once re-proven unchanged."""
    copies = _regular_copies(backup_dir, filename)
    for stale in copies[: max(0, len(copies) - retention)]:
        if not _still_as_taken(stale):
            logger.warning("instruction_backup_changed_kept", path=str(stale))
            continue
        try:
            # ``unlink`` never recurses: an entry swapped for a directory after the scan fails here, not deleted.
            stale.unlink()
        except OSError:
            # A copy we could not prune is a disk-space concern, not a data-loss
            # one: the write it protects has not happened yet and the newest
            # copies are intact. Log rather than refuse.
            logger.warning("instruction_backup_prune_failed", path=str(stale), exc_info=True)


def _newest_copy(backup_dir: Path, filename: str) -> Path | None:
    """Return the newest retained regular-file copy of *filename* (stamps sort chronologically), or ``None``."""
    copies = _regular_copies(backup_dir, filename)
    return copies[-1] if copies else None


def _holds(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:  # trw-fail-silent-allow: unreadable means unproven
        return False


def prepare_backup_dir(project_root: Path, backup_dir: str) -> Path:
    """The backup directory, contained and created BEFORE any write (fail-closed: a write without one is refused).

    Raises:
        BackupRefused: when it escapes *project_root* or cannot be created.
    """
    directory = resolve_backup_dir(project_root, backup_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupRefused("backup_failed", f"could not create backup directory {directory}: {exc}") from exc
    return directory


def _place(displaced: Path, directory: Path, pattern: str) -> Path:
    """Give *displaced* a new name in *directory* with a no-replace ``link``, then drop its capture-side name.

    The inode keeps the new name, so dropping the old one loses nothing; a name already taken (by anything) is
    never replaced, a fresh stamp is tried instead (codex r2). Raises ``OSError`` when no link can be made.
    """
    for _ in range(8):
        dest = directory / (pattern % datetime.now(timezone.utc).strftime(_BACKUP_STAMP_FORMAT))
        try:
            os.link(displaced, dest, follow_symlinks=False)
        except FileExistsError:  # trw-fail-silent-allow: taken; the next stamp is tried
            continue
        displaced.unlink()
        return dest
    raise FileExistsError(errno.EEXIST, "no free backup name", str(directory))


def keep_displaced(displaced: Path, directory: Path, filename: str, judged: str, retention: int) -> Path:
    """Make the file a write displaced the backup: move it into *directory* by rename, never copy it.

    *displaced* holds exactly *judged* (the publish re-proved it). It gets a new name carrying its stamp and content
    hash; no retained copy is ever replaced, because a program that kept the user's former file open can still write
    to it (PUBLISH-RACE-HARDEN codex r1). When the newest copy already holds these bytes the duplicate is dropped
    instead, once re-proven unchanged. Retention then prunes the oldest copies it can re-prove
    unchanged. The capture folder it came from loses only its own ``meta.json`` and goes only when empty. Raises
    ``OSError`` when the rename is refused (another filesystem): the caller reports the capture as the backup.
    """
    data = judged.encode("utf-8")
    newest = _newest_copy(directory, filename)
    folder = displaced.parent
    if newest is not None and _holds(newest, data) and _holds(displaced, data):
        # E2E-INC-015: those bytes are already retained, so the duplicate goes, and only once re-proven unchanged
        # (a write landing between this read and the unlink is the documented residual). The retained copy itself
        # is never touched.
        displaced.unlink()
        dest = newest
    else:
        dest = _place(displaced, directory, f"{filename}.%s.{hashlib.sha256(data).hexdigest()[:16]}")
    with contextlib.suppress(OSError):  # TRW's own record of the capture
        (folder / "meta.json").unlink()
    with contextlib.suppress(OSError):  # only an EMPTY folder goes
        folder.rmdir()
    _prune_retention(directory, filename, retention)
    return dest


__all__ = ["BackupRefused", "keep_displaced", "prepare_backup_dir", "resolve_backup_dir"]
