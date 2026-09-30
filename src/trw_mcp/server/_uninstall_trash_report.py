"""Uninstall's report of TRW captures moved on to the macOS system Trash.

Belongs to the ``_subcommands_lifecycle.py`` ``_run_uninstall`` facade. Split out to keep it under the
350 effective-LOC gate.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from trw_mcp.bootstrap._safe_remove import (
    delete_proven_unchanged_captures,
    move_captures_to_os_trash,
    trash_dir,
)


def _move_matched_captures_to_os_trash(
    apply_result: dict[str, list[str]], target: Path, display: Callable[[Path, Path], str]
) -> None:
    """Uninstall is an explicit full removal: TRW's own unchanged captures leave ``.trw/trash`` for the macOS
    system Trash by rename (nothing is unlinked, so a late fd write survives there); the rest stay and are listed.
    Off macOS there is no system Trash: a capture whose ``meta.json`` hash still matches its bytes is deleted, and
    any capture that cannot be proven unchanged stays in ``.trw/trash`` with the reason printed.
    """
    pairs = list(zip(apply_result.get("trashed", []), apply_result.get("trashed_at", []), strict=True))
    captures = [Path(at) for _orig, at in pairs if at]
    if sys.platform == "darwin":
        dest, kept = move_captures_to_os_trash(target, captures)
        if dest is not None:
            print(f"  Moved {len(captures) - len(kept)} unchanged TRW file(s) to the system Trash: {dest}")
    else:
        deleted, kept = delete_proven_unchanged_captures(target, captures)
        if deleted:
            print(f"  Removed {deleted} unchanged TRW file(s) (byte-identical to what TRW wrote; no system Trash here)")
    left = {str(data): why for data, why in kept}
    remove = f"remove with: rm -rf {shlex.quote(str(trash_dir(target)))}"
    for orig, at in pairs:
        if at and at in left:  # E2E-INC-062: on either platform, say why the capture stayed and how to remove it
            print(f"  Kept in .trw/trash: {display(Path(orig), target)} ({left[at]}; see doctor; {remove})")
        elif not at:
            print(f"  Moved to .trw/trash: {display(Path(orig), target)} (unchanged TRW file; see doctor)")
    # Every capture moved on: leave no empty .trw/trash behind, even on a refused run that keeps .trw.
    if captures and not kept:
        try:
            os.rmdir(trash_dir(target))
        except OSError:  # trw-fail-silent-allow: not empty (another capture) or already gone; rmdir only
            pass
