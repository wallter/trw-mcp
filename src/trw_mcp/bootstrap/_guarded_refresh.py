"""The race-safe steps of a hook or skill refresh (GUARDED-COPY-UPDATE-RACE).

Belongs to the ``_template_updater`` facade, which re-exports these names. A refresh checks that a file is not
user-modified and then writes it; these helpers make sure a writer landing between the check and the write
keeps its bytes: an existing file is claimed only if it still holds the checked bytes, and a name that was
absent at the check is published with an exclusive create.
"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import structlog

from trw_mcp._checkout_write import record_run_write

from ._safe_remove import remove_if_hash

logger = structlog.get_logger(__name__)


def _create_if_absent(src: Path, dest: Path, result: dict[str, list[str]], *, make_executable: bool) -> None:
    """Publish *src* at *dest* with an exclusive create; a file a writer put there after the check is kept."""
    from ._restore_proof import copy_back_exclusive

    notes: list[str] = []
    data = src.read_bytes()
    try:
        # Exec bits set through the create's own fd: a name swapped for a symlink after the create is never
        # chmod-ed (lead r7 c5). Same bits as _update_or_report.
        executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if make_executable else 0
        placed = copy_back_exclusive(src, dest, str(dest), notes, add_mode=executable, narrow_by_umask=True)
    except OSError as exc:  # trw-fail-silent-allow: not silent; reported as an error like any failed refresh
        result.setdefault("errors", []).append(f"Failed to copy {src} -> {dest}: {exc}")
        return
    if not placed:
        logger.info("artifact_changed_during_update", path=str(dest))
        result.setdefault("modified", []).append(str(dest))
        return
    record_run_write(dest, data)  # this run's own write: a rollback may clear it on proof


def _sha_of_regular_file(path: Path) -> str | None:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # trw-fail-silent-allow: no hash means no claim; the legacy refresh path decides as before
        return None


def _claim_for_refresh(dest: Path, root: Path, checked: str, key: str) -> bool:
    """Take *dest* out of the way only if it still holds the *checked* bytes (GUARDED-COPY-UPDATE-RACE).

    A writer could land between the "not user-modified" check and the write. remove_if_hash renames the file
    into a capture and re-verifies it; a mismatch is put back, so the writer's bytes are never replaced. A
    verified capture holds exactly the checked (TRW-owned) bytes, so it is discarded on that proof.
    """
    from trw_memory._tree_removal import remove_tree

    outcome = remove_if_hash(dest, root, checked, key=key)
    if outcome.status == "absent":
        return True
    if outcome.status != "removed":
        return False
    if outcome.retained_at is not None:
        capture = outcome.retained_at.parent
        remove_tree(capture, purpose="verified pre-refresh capture")
        try:
            capture.parent.rmdir()  # the capture was the only thing in .trw/trash: leave no empty folder behind
        except OSError:  # trw-fail-silent-allow: not empty (a user's capture lives there) or busy: it stays
            pass
    return True
