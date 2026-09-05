"""Pre-write backup and retention for instruction files (PRD-FIX-123-FR04).

Belongs to the ``_write_guard.py`` seam; nothing else calls it. Split out so the
guard stays a single readable decision procedure and this stays a single
readable filesystem procedure, both under the module-size gate.

Posture is fail-CLOSED and the caller depends on it: every function here raises
on failure so the guard REFUSES the write. A degraded instruction file is
recoverable; destroyed user content is not (PRD-FIX-123-NFR02).
"""

from __future__ import annotations

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
    ``../../tmp/x`` would otherwise let TRW write outside the project. Unlike
    the PRD-CORE-203 sidecar — which degrades to inline on an escaping path —
    an escaping backup directory REFUSES the write, because the backup is the
    only thing standing between a bad candidate and unrecoverable user content.

    Raises:
        BackupRefused: when the resolved directory escapes *project_root*.
    """
    # Lazy import: ``_instruction_carrier`` reaches back into this package, and
    # importing it at module scope would close a cycle through ``_write_guard``.
    from trw_mcp.state.claude_md._instruction_carrier import is_path_within

    candidate = project_root / backup_dir
    if not is_path_within(project_root, candidate):
        raise BackupRefused(
            "backup_path_escape",
            f"instruction_backup_dir {backup_dir!r} resolves outside the project root {project_root}",
        )
    return candidate


def _prune_retention(backup_dir: Path, filename: str, retention: int) -> None:
    """Keep at most *retention* copies of *filename*, oldest pruned first.

    A bounded ``glob`` over one filename's siblings, never a recursive walk
    (PRD-FIX-123-NFR01).
    """
    copies = sorted(p for p in backup_dir.glob(f"{filename}.*") if p.is_file())
    for stale in copies[: max(0, len(copies) - retention)]:
        try:
            stale.unlink()
        except OSError:
            # A copy we could not prune is a disk-space concern, not a data-loss
            # one: the write it protects has not happened yet and the newest
            # copies are intact. Log rather than refuse.
            logger.warning("instruction_backup_prune_failed", path=str(stale), exc_info=True)


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
