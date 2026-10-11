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

#: Captures kept for one shared reason that are still named one per line; past this the reason is said once.
_NAMED_PER_REASON = 3


def _take_trw_only_captures(apply_result: dict[str, list[str]], target: Path) -> None:
    """Add this run's captures of instruction files that held only TRW's blocks (CLAUDE-MD S2) to *apply_result*."""
    from trw_mcp.server._subcommands_uninstall_config import TRW_ONLY_CAPTURES

    for path, capture in [(p, c) for p, c in TRW_ONLY_CAPTURES.items() if p.is_relative_to(target)]:
        del TRW_ONLY_CAPTURES[path]
        apply_result.setdefault("trashed", []).append(str(path))
        apply_result.setdefault("trashed_at", []).append(str(capture))


def _move_matched_captures_to_os_trash(
    apply_result: dict[str, list[str]], target: Path, display: Callable[[Path, Path], str]
) -> None:
    """Uninstall is an explicit full removal: TRW's own unchanged captures leave ``.trw/trash`` for the macOS
    system Trash by rename (nothing is unlinked, so a late fd write survives there); the rest stay and are listed.
    Off macOS there is no system Trash: a capture whose ``meta.json`` hash still matches its bytes is deleted, and
    any capture that cannot be proven unchanged stays in ``.trw/trash`` with the reason printed.
    """
    _take_trw_only_captures(apply_result, target)
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
    by_reason: dict[str, list[str]] = {}
    for orig, at in pairs:
        if at and at in left:
            by_reason.setdefault(left[at], []).append(display(Path(orig), target))
        elif not at:
            print(f"  Moved to .trw/trash: {display(Path(orig), target)} (unchanged TRW file; see doctor)")
    # E2E-INC-062: on either platform, say why each capture stayed and how to remove it. One reason shared by many
    # captures (no usable system Trash: one per removed file, 79 lines for three clients in a 9.5.0 report) is said
    # once, with the first few names; doctor lists every capture.
    for why, names in by_reason.items():
        if len(names) <= _NAMED_PER_REASON:
            for name in names:
                print(f"  Kept in .trw/trash: {name} ({why}; see doctor; {remove})")
        else:
            shown = ", ".join(names[:_NAMED_PER_REASON])
            more = len(names) - _NAMED_PER_REASON
            print(f"  Kept {len(names)} files in .trw/trash ({why}; see doctor; {remove}): {shown} and {more} more")
    # Every capture moved on: leave no empty .trw/trash behind, even on a refused run that keeps .trw -- unless it was
    # there before this uninstall began, which makes it not this run's to remove (CLAUDE-MD S1 red team).
    from trw_mcp.server._uninstall_corpus import PREEXISTING_TRASH

    if captures and not kept and trash_dir(target) not in PREEXISTING_TRASH:
        try:
            os.rmdir(trash_dir(target))
        except OSError:  # trw-fail-silent-allow: not empty (another capture) or already gone; rmdir only
            pass
