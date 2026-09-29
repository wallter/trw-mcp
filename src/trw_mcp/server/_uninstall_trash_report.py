"""Uninstall's report of TRW captures moved on to the macOS system Trash.

Belongs to the ``_subcommands_lifecycle.py`` ``_run_uninstall`` facade. Split out to keep it under the
350 effective-LOC gate.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from trw_mcp.bootstrap._safe_remove import move_captures_to_os_trash, trash_dir


def _move_matched_captures_to_os_trash(
    apply_result: dict[str, list[str]], target: Path, display: Callable[[Path, Path], str]
) -> None:
    """Uninstall is an explicit full removal: TRW's own unchanged captures leave ``.trw/trash`` for the macOS
    system Trash by rename (nothing is unlinked, so a late fd write survives there); the rest stay and are listed.
    """
    pairs = list(zip(apply_result.get("trashed", []), apply_result.get("trashed_at", []), strict=True))
    captures = [Path(at) for _orig, at in pairs if at]
    dest, kept = move_captures_to_os_trash(target, captures)
    if dest is not None:
        print(f"  Moved {len(captures) - len(kept)} unchanged TRW file(s) to the system Trash: {dest}")
    left = {str(data) for data, _why in kept}
    for orig, at in pairs:
        if not at or at in left:
            print(f"  Moved to .trw/trash: {display(Path(orig), target)} (unchanged TRW file; see doctor)")
    # Every capture moved on: leave no empty .trw/trash behind, even on a refused run that keeps .trw.
    if captures and not kept:
        try:
            os.rmdir(trash_dir(target))
        except OSError:  # trw-fail-silent-allow: not empty (another capture) or already gone; rmdir only
            pass
