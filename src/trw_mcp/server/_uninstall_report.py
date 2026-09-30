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


def print_done(target: Path, removed: int, remove_ide: str | None, delete_memory: bool) -> None:
    """The uninstall summary; on a full uninstall, first prune the scaffold directories left empty (INC-080)."""
    from trw_mcp.bootstrap._uninstall_skill_dir import prune_scaffold_dirs
    from trw_mcp.server import _uninstall_memory

    if not remove_ide:
        prune_scaffold_dirs(target)
    print(f"\n  Done. Removed {removed} item(s).")
    if remove_ide:
        print(f"  {remove_ide} surfaces removed. Other clients and framework-core files are untouched.")
    else:
        store = _uninstall_memory.shared_store()
        if not delete_memory and store.exists():
            print(f"  Note: the shared memory store {store} is kept, with this checkout's rows in it.")
            print("  Re-run with --delete-memory to delete this checkout's namespace from it.")
        print("  To uninstall the package itself: pip uninstall trw-mcp trw-memory")


def report_custom_format_kept(path: Path, target: Path) -> int:
    """Name a merged config left untouched with its TRW entries; return the error count (1 when named).

    E2E-UNINSTALL-CUSTOM-JSON: TRW never rewrites a file the user formatted, so its entries stay; that is a
    partial uninstall, reported here and in the exit status, never a silent success. A file not recorded as
    custom-formatted (nothing of TRW's was in it) prints nothing and counts 0.
    """
    from trw_mcp.server._uninstall_hook_strips import CUSTOM_FORMAT

    if path not in CUSTOM_FORMAT:
        return 0
    print(
        f"  Kept: {display(path, target)} (custom-formatted, so left untouched with its TRW entries;"
        " remove the `trw` server and TRW hook entries by hand)"
    )
    return 1


def report_stripped(path: Path, target: Path) -> int:
    """The line for a config uninstall rewrote; return the error count (1 when edited TRW hooks remain).

    INC-012 follow-up: "removed TRW entries" was printed even when TRW hooks were left behind. A TRW hook the
    user edited is kept by design, but it runs a script uninstall deleted, so the run says so and fails.
    """
    from trw_mcp.server._uninstall_hook_strips import KEPT_EDITED

    left = KEPT_EDITED.get(path)
    if not left:
        print(f"  Cleaned: {display(path, target)} (removed TRW entries)")
        return 0
    print(
        f"  Kept: {display(path, target)} (removed TRW entries, but kept {len(left)} TRW hook(s) you edited, "
        f"which run scripts uninstall removed; remove them by hand: {'; '.join(left)[:300]})"
    )
    return 1


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
