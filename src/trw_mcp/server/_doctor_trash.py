"""The ``trw_trash`` row of ``trw-mcp doctor``: what ``.trw/trash`` holds.

``remove_if_hash`` moves a file it is about to remove into ``.trw/trash/<capture>/``
and TRW never deletes those backups automatically, so this row is where the
operator learns the folder exists and how big it has grown. Read-only: it never
removes, creates or follows anything. Bounded: it stops counting after
``MAX_SCANNED`` directory entries and reports the totals as lower bounds.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Literal

__all__ = ["MAX_SCANNED", "WARN_BYTES", "trash_row"]

MAX_SCANNED = 10_000
WARN_BYTES = 50 * 1024 * 1024


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # unreachable; keeps the type checker satisfied


def _scan(trash: Path) -> tuple[int, int, bool]:
    """``(capture folders, regular-file bytes, truncated)``; symlinks are counted as nothing and not followed."""
    folders = total = scanned = 0
    stack = [(str(trash), 0)]
    while stack:
        path, depth = stack.pop()
        with os.scandir(path) as it:
            for entry in it:
                scanned += 1
                if scanned > MAX_SCANNED:
                    return folders, total, True
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders += depth == 0
                    stack.append((entry.path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return folders, total, False


def trash_row(target: Path, _config: object) -> tuple[Literal["PASS", "WARN"], str]:
    """PASS when absent/empty/small; WARN over 50 MB or when unreadable."""
    trash = target / ".trw" / "trash"
    try:
        if trash.is_symlink():
            return "WARN", f"{trash} is a symlink; not followed"
        if not trash.exists():
            return "PASS", f"{trash} is absent"
        folders, total, truncated = _scan(trash)
    except OSError as exc:
        return "WARN", f"cannot read {trash}: {exc}"
    if folders == 0 and total == 0:
        return "PASS", f"{trash} is empty"
    bound = "≥" if truncated else ""
    size = f"{bound}{_human(total)}"
    count = f"{bound}{folders}"
    # E2E-INC-062: name the exact command at ANY size -- TRW never deletes these backups itself.
    remove = f"remove with: rm -rf {shlex.quote(str(trash))}"
    if total > WARN_BYTES:
        return (
            "WARN",
            f"`.trw/trash` holds {size} in {count} backups; delete it when you no longer need them ({remove})",
        )
    return "PASS", f"{trash} holds {size} in {count} backup(s); {remove} once you no longer need them"
