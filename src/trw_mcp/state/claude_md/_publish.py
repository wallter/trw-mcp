"""Publish an instruction file without ever losing a save made while it is written (CLAUDE-MD S1, PUBLISH-RACE-HARDEN).

Belongs to the ``_write_guard.py`` seam, and is built on the bootstrap publish primitives (``_proven_replace``, from
``_trash`` and FB-01-KI1-RACE), so the guard never compares-then-replaces:

* An existing file: the new bytes are staged complete in a private folder, the file is captured by rename into
  ``.trw/trash`` and re-proven there by its content, and only then is the staged file linked at the free name with a
  no-replace link. A save made at any point keeps its bytes: before the capture it fails the re-proof and goes back;
  after it, it holds the name and the link is refused. A link failure gives the name back to the displaced file.
* The displaced file (exactly the bytes the write was judged on) then BECOMES the backup: it is moved into the backup
  directory by rename, never copied. Where that rename is refused (another filesystem, for one) it stays in its
  capture folder and that path is reported as the backup; it is never copied and unlinked.
* A missing file is created the same way, staged then linked, with the umask applied to its mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from trw_mcp.state._containment import assert_trw_write_contained


class Published(NamedTuple):
    """``blocked`` is why the file was left as found (None: published); ``backup`` where the previous version is.

    ``failed``: the write itself failed (not a concurrent change), so the caller reports ``write_failed``.
    """

    blocked: str | None
    backup: Path | None
    failed: bool = False


def publish(
    target: Path, root: Path, candidate: str, judged: str | None, backup_dir: Path | None, retention: int
) -> Published:
    """Put *candidate* at *target* only if it still holds *judged* (``None``: nothing is there)."""
    from trw_mcp._checkout_write import record_run_write
    from trw_mcp.bootstrap._proven_replace import create_exclusive, replace_proven
    from trw_mcp.state.claude_md._write_backup import keep_displaced

    assert_trw_write_contained(target)
    if judged is None:
        target.parent.mkdir(parents=True, exist_ok=True)
        made = create_exclusive(target, root, candidate.encode("utf-8"))
        if made.status != "replaced":
            return Published(made.reason, None, made.failed)
        record_run_write(target, candidate.encode("utf-8"))  # an update's rollback may clear it on proof (KI1-RACE)
        return Published(None, None)
    outcome = replace_proven(target, root, judged.encode("utf-8"), candidate.encode("utf-8"))
    backup = outcome.previous
    if backup is not None and backup_dir is not None and outcome.status == "replaced":
        try:
            backup = keep_displaced(backup, backup_dir, target.name, judged, retention)
        except OSError:  # trw-fail-silent-allow: not silent; the capture itself is reported as the backup
            pass
    if outcome.status != "replaced":
        where = f"; the version TRW read is kept at {backup}" if backup is not None else ""
        return Published(f"{outcome.reason}{where}", backup, outcome.failed)
    record_run_write(target, candidate.encode("utf-8"))  # an update's rollback may clear it on proof (KI1-RACE)
    return Published(None, backup)
