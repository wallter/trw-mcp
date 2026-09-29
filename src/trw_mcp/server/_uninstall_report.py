"""Uninstall reporting: why an item was refused, and the keep-``.trw`` decision's guidance.

Split out of ``_subcommands_lifecycle`` (behaviour unchanged). Reads only link text with ``os.readlink``; a link
target is never resolved, opened or touched.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from trw_mcp.bootstrap._safe_remove import path_refusal
from trw_mcp.bootstrap._uninstall_manifest import KeyDisposition, SurfaceDisposition
from trw_mcp.bootstrap._utils import printable
from trw_mcp.server._subcommands_uninstall_config import REFUSAL_REASONS


def display(path: Path, target: Path) -> str:
    """Render *path* relative to *target* when possible, else absolute, with control characters escaped.

    Every uninstall line that names a file goes through here, so a file name holding an escape sequence can
    never drive the user's terminal.
    """
    try:
        return printable(str(path.relative_to(target)))
    except ValueError:
        return printable(str(path))


def refusal_text(path: Path, root: Path) -> str:
    """The real reason a managed/merged file was refused: a symlink, another guard reason, or a read failure."""
    reason = REFUSAL_REASONS.get(path) or path_refusal(path, root)
    if reason is None:
        return "refused (could not be read)"
    # A guard reason names a path (``refused: parent <dir> is a symlink``); escape it like every other echo.
    return "refused (symlink)" if "symlink" in reason else printable(reason)


def print_symlink_guidance(refused: list[Path], target: Path) -> None:
    """Name each symlink that made uninstall refuse an item, with its target, and how to fix it."""
    links: dict[str, str] = {}
    for path in refused:
        current = target
        if not path.is_relative_to(target):
            continue
        for part in path.relative_to(target).parts:
            current = current / part
            if current.is_symlink():
                try:
                    links.setdefault(display(current, target), printable(os.readlink(current)))
                except OSError as exc:
                    links.setdefault(display(current, target), f"<unreadable link target: {type(exc).__name__}>")
                break
    if not links:
        return
    print("\n  Refused, because these paths are symlinks (TRW never deletes through a symlink):", file=sys.stderr)
    for rel, link_target in links.items():
        print(f"    {rel} -> {link_target}", file=sys.stderr)
    print(
        "  Fix: replace each symlink with a real directory (copy its contents in), then re-run uninstall.",
        file=sys.stderr,
    )


def report_kept_trw(plain_paths: list[Path], refused: list[Path], trw_left: list[str], target: Path) -> int:
    """Print why ``.trw`` (the manifest) stays; return the extra error count (1 when a TRW file was kept).

    Keeping ``.trw`` is a partial uninstall: the caller exits 1, and a re-run after the user fixes the cause can
    still classify the residue.
    """
    for p in plain_paths:
        print(
            f"  Kept: {display(p, target)} "
            "(an item was refused or a TRW file was kept; the manifest stays so a re-run can finish)"
        )
    print_symlink_guidance(refused, target)
    for left in trw_left:
        print(
            f"  Kept .trw because TRW files remain under {display(Path(left), target)} "
            "(a file TRW cannot prove is user-owned, e.g. a symlinked child); "
            "remove or fix it and re-run uninstall.",
            file=sys.stderr,
        )
    return 1 if trw_left else 0


def planned_refusals(
    managed_paths: list[Path],
    merged_config_paths: list[tuple[Path, Path, str]],
    covered: list[KeyDisposition],
    uncovered: list[SurfaceDisposition],
    target: Path,
) -> list[Path]:
    """Every item a ``--dry-run`` predicts the real run would refuse (a symlink, an unsafe path).

    Runs the same guards as the real removal, without writing, so the dry-run can say ``.trw`` would be kept.
    """
    from trw_mcp.server._subcommands_uninstall_config import (
        _remove_managed_block_file,
        _strip_trw_from_merged_config,
    )

    refused = [d.path for d in covered if d.action == "rejected-unsafe"]
    refused += [u.path for u in uncovered if u.action == "refused"]
    refused += [p for p in managed_paths if _remove_managed_block_file(p, target, dry_run=True) == "refused"]
    refused += [
        p
        for p, root, shape in merged_config_paths
        if _strip_trw_from_merged_config(p, root, dry_run=True, shape=shape) == "refused"
    ]
    return refused
