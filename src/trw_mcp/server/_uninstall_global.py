"""Uninstall's handling of user-global client configs (files outside the project, shared by every project).

A ``home_scoped`` surface such as ``~/.gemini/config/mcp_config.json`` holds one ``mcpServers.trw`` entry that is
identical for every project on the machine, so a project uninstall must not edit it. Only ``--global`` does, under
the same exact-match and canonical-format checks as any merged config. Without it the file is reported and left
byte-identical. The rule is keyed on the surface's ``home_scoped`` flag, never on a path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trw_mcp.client_profiles.catalog import UninstallSurface
from trw_mcp.server._subcommands_uninstall_config import CUSTOM_FORMAT, QUIET, _strip_trw_from_merged_config
from trw_mcp.server._uninstall_report import display, planned_refusals

_CUSTOM_FORMAT_NOTE = "TRW entry present but the file isn't in TRW's formatting; left untouched, remove it by hand"


class GlobalConfigs:
    """The user-global files one uninstall run found, and whether ``--global`` lets it edit them."""

    def __init__(self, include: bool) -> None:
        self.include = include
        self.paths: list[Path] = []
        self.checked: dict[Path, str | None] = {}  # the match check's result per file, so no path is checked twice

    def defers(self, surface: UninstallSurface, path: Path) -> bool:
        """Record a home-scoped surface; True when uninstall must not edit it (no ``--global``)."""
        if not surface.home_scoped:
            return False
        self.paths.append(path)
        return not self.include

    def report_left(self, target: Path, *, dry_run: bool) -> None:
        """Say each deferred file is not edited without ``--global``."""
        if self.include:
            return
        verb = "would be left in place" if dry_run else "left in place"
        for p in self.paths:
            print(
                f"  {verb}: {display(p, target)} mcpServers.trw is shared by all projects; "
                "run `trw-mcp uninstall --global` to remove it"
            )

    def entry_line(self, p: Path, root: Path, shape: str, target: Path, *, dry_run: bool) -> str:
        """The listing line for a merged config file; a ``--global`` file runs the real match check, unwritten."""
        if p not in self.paths:
            return f"    entry {display(p, target)} (TRW entries only)"
        quiet = QUIET.set(not dry_run)  # a real run's own pass does the warning; a dry run has only this one
        try:
            status = self.checked[p] = _strip_trw_from_merged_config(p, root, dry_run=True, shape=shape)
        finally:
            QUIET.reset(quiet)
        will = "would" if dry_run else "will"
        if status in ("stripped", "removed"):
            outcome = f"TRW entry matches; {will} be removed"
        elif p in CUSTOM_FORMAT:
            outcome = _CUSTOM_FORMAT_NOTE
        else:
            outcome = f"no matching TRW entry; {will} be kept"
        return f"    entry {display(p, target)} ({outcome})"

    def strip(self, p: Path, root: Path, shape: str) -> str | None:
        """The real strip of a merged config; a global file is re-read just before it is written."""
        return _strip_trw_from_merged_config(p, root, dry_run=False, shape=shape, verify_unchanged=p in self.paths)

    def refusals(self, managed: list[Path], merged: list[tuple[Path, Path, str]], *rest: Any) -> list[Path]:
        """What a ``--dry-run`` predicts would be refused; the listing already checked the global files."""
        return planned_refusals(managed, [m for m in merged if m[0] not in self.checked], *rest) + [
            p for p, status in self.checked.items() if status == "refused"
        ]

    def report_kept(self, p: Path, status: str | None, target: Path) -> int:
        """Say a global file kept its entry; returns 1 (an error) when another writer changed it mid-run."""
        if status == "changed":
            print(
                f"  Error updating {display(p, target)}: changed while uninstall ran; left as the other writer wrote it"
            )
            return 1
        why = _CUSTOM_FORMAT_NOTE if p in CUSTOM_FORMAT else "no entry matching TRW's generated value"
        print(f"  Kept: {display(p, target)} ({why})")
        return 0
