"""What ``update-project`` left alone, named one file per line (FB-INSTALL-03).

An 8.1.2 upgrade over an existing project printed nothing about an edited ``lib-trw.sh`` it kept (while replacing the
hooks that source it), a git-dirty ``AGENTS.md`` it did not refresh, or a retired skill it could not prove it wrote:
the first was recorded in ``result["modified"]``, which nothing read, and the others only as a count in
``preserved``. The lines here use the ``WARNING: <text>`` contract ``install-trw.py``'s progress reader keys on, so
they also survive its spinner.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._utils import printable

logger = structlog.get_logger(__name__)

#: ``preserved`` entries that carry a reason in parentheses, and what to tell the user about each.
#: Everything else in ``preserved`` (``.trw/config.yaml`` and the like) is kept on every run by design.
_KEPT_REASONS = {
    "(uncommitted_changes)": "it has uncommitted changes in git, so this update did not refresh it",
    "(not_installer_owned)": "TRW cannot show it wrote it (edited, or never recorded), so this update left it in place",
    "(uncommitted_changed_after_keep)": (
        "it has uncommitted changes in git, so this update kept your version, but later steps then changed TRW's "
        "own entries in it; run git diff on it to see exactly what changed"
    ),
}
_KEPT_EDITED = "you edited it since TRW last wrote it, so this update did not replace it"

__all__ = ["kept_files", "print_kept", "print_retired", "report_kept"]


def _display_path(path: str, target: Path) -> str:
    """*path* relative to the project when it is inside it, else as given."""
    try:
        return Path(path).relative_to(target).as_posix()
    except ValueError:
        return path


def kept_files(result: dict[str, list[str]], target: Path) -> list[tuple[str, str]]:
    """``(path, why)`` for every file this update left alone, once each, in report order.

    The two producers: ``modified`` (a hook, skill or agent the user edited, recorded by the raw-copy guard and
    read by nothing until now) and ``preserved`` entries that carry a reason, which the summary only counts.
    """
    kept: dict[str, str] = {}
    for path in result.get("modified", []):
        kept.setdefault(_display_path(str(path), target), _KEPT_EDITED)
    for entry in result.get("preserved", []):
        for suffix, why in _KEPT_REASONS.items():
            if str(entry).endswith(suffix):
                kept.setdefault(_display_path(str(entry).removesuffix(suffix).rstrip(), target), why)
    return list(kept.items())


def print_kept(result: dict[str, list[str]], target: Path) -> None:
    """One ``WARNING: kept <path>: <why>`` line per file left alone, so the installer re-surfaces it.

    Printed before the summary, like warnings: a run that kept an edited hook library while replacing the hooks
    that source it is exactly the run whose summary is skipped on error.
    """
    for path, why in kept_files(result, target):
        print(f"WARNING: kept {printable(path)}: {why}")


def print_retired(paths: list[str], described: list[str] | None = None) -> None:
    """One line per retired TRW file deleted in place (TRW's own bytes, or committed in git).

    A path a warning already describes (``"<path>: ..."``, e.g. a git-recoverable removal with its restore
    command, or an edited hook-family file) is skipped: the warning says more (E2E-INC-133).
    """
    for path in [p for p in paths if not any(str(w).startswith(f"{p}: ") for w in described or [])]:
        print(f"Removed retired TRW file: {printable(path)}")


def report_kept(result: dict[str, list[str]], target: Path, *, detailed: bool, quiet: bool) -> None:
    """Name what the update left alone: structured log lines under ``-v``, plain lines otherwise, nothing if quiet."""
    if detailed:
        for path, why in kept_files(result, target):
            logger.warning("update_project_kept", op="update_project", path=path, detail=why)
    elif not quiet:
        print_kept(result, target)
