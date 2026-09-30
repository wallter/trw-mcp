"""Pre-write backup and retention for instruction files (PRD-FIX-123-FR04).

Belongs to the ``_write_guard.py`` seam; nothing else calls it. Split out so the
guard stays a single readable decision procedure and this stays a single
readable filesystem procedure, both under the module-size gate.

Posture is fail-CLOSED and the caller depends on it: every function here raises
on failure so the guard REFUSES the write. A degraded instruction file is
recoverable; destroyed user content is not (PRD-FIX-123-NFR02).
"""

from __future__ import annotations

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


def _prune_retention(backup_dir: Path, filename: str, retention: int) -> None:
    """Keep at most *retention* copies of *filename*, oldest pruned first."""
    copies = _regular_copies(backup_dir, filename)
    for stale in copies[: max(0, len(copies) - retention)]:
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


def backup_instruction_file(
    target: Path,
    current: str,
    *,
    project_root: Path,
    backup_dir: str,
    retention: int,
) -> str:
    """Copy *current* (the PRE-write bytes of *target*) into the backup directory.

    Returns the backup path. Raises :class:`BackupRefused` on any failure so the
    caller refuses the write — never proceeds unprotected.
    """
    directory = resolve_backup_dir(project_root, backup_dir)
    newest = _newest_copy(directory, target.name)
    if newest is not None:
        try:
            if newest.read_text(encoding="utf-8") == current:
                # The newest copy already holds these exact bytes; a second one is disk churn, not protection.
                return str(newest)
        except (OSError, UnicodeDecodeError):  # trw-fail-silent-allow: unreadable newest copy -> take a fresh one
            pass
    stamp = datetime.now(timezone.utc).strftime(_BACKUP_STAMP_FORMAT)
    copy_path = directory / f"{target.name}.{stamp}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        copy_path.write_text(current, encoding="utf-8")
    except OSError as exc:
        raise BackupRefused("backup_failed", f"could not write backup {copy_path}: {exc}") from exc

    _prune_retention(directory, target.name, retention)
    return str(copy_path)


__all__ = ["BackupRefused", "backup_instruction_file", "resolve_backup_dir"]
