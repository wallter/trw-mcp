"""Managed .gitignore section for TRW channel infrastructure.

Provides idempotent add/remove of entries inside a sentinel-bounded
managed section.  Content outside the section is byte-identical after
every operation (FR21, PRD-DIST-2400).

Sentinel markers:
    # TRW:MDC:BEGIN
    ... managed entries ...
    # TRW:MDC:END

PRD-DIST-2400 FR21.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "GITIGNORE_BEGIN",
    "GITIGNORE_END",
    "add_gitignore_entry",
    "list_gitignore_entries",
    "remove_gitignore_entry",
]

GITIGNORE_BEGIN = "# TRW:MDC:BEGIN"
GITIGNORE_END = "# TRW:MDC:END"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_managed_section(content: str) -> tuple[int, int] | None:
    """Return (begin_line_idx, end_line_idx) of the managed section or None.

    The returned indices are the line numbers of the BEGIN and END sentinel
    lines themselves (zero-based), so the entries live on lines
    (begin_line_idx+1) .. (end_line_idx-1).

    Returns None if either sentinel is absent or if END precedes BEGIN.
    """
    lines = content.splitlines()
    begin_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip() == GITIGNORE_BEGIN and begin_idx is None:
            begin_idx = i
        elif line.rstrip() == GITIGNORE_END and begin_idx is not None:
            end_idx = i
            break
    if begin_idx is None or end_idx is None:
        return None
    return begin_idx, end_idx


def _read_content(gitignore_path: Path) -> str:
    """Return the .gitignore content, or empty string if the file is absent."""
    try:
        return gitignore_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_content(gitignore_path: Path, content: str) -> None:
    """Write *content* to *gitignore_path*, creating parent dirs as needed."""
    gitignore_path.parent.mkdir(parents=True, exist_ok=True)
    gitignore_path.write_text(content, encoding="utf-8")


def _rebuild(lines: list[str]) -> str:
    """Join *lines* back into a file content string.

    Preserves the original line-ending convention (no trailing newline added
    beyond what was already present).
    """
    if not lines:
        return ""
    text = "\n".join(lines)
    # Preserve trailing newline if the last line was empty
    if lines[-1] == "":
        return text
    return text + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_gitignore_entry(repo_root: Path, entry: str) -> bool:
    """Idempotently add *entry* inside the managed section.

    Creates the managed section and the ``.gitignore`` file if absent.
    Content outside the managed section is byte-identical.

    Args:
        repo_root: Repository root directory.
        entry: Line to add (e.g. ``".trw/telemetry/channel-events.jsonl"``).

    Returns:
        True if the entry was added; False if it was already present.
    """
    gitignore_path = repo_root / ".gitignore"
    content = _read_content(gitignore_path)
    lines = content.splitlines()

    section = _get_managed_section(content)
    if section is not None:
        begin_idx, end_idx = section
        # Check whether entry is already inside the section
        interior = lines[begin_idx + 1 : end_idx]
        if entry in interior:
            log.debug(
                "gitignore_entry_already_present",
                repo_root=str(repo_root),
                entry=entry,
                outcome="skipped",
            )
            return False
        # Insert before the END sentinel
        lines.insert(end_idx, entry)
        _write_content(gitignore_path, _rebuild(lines))
        log.debug(
            "gitignore_entry_added",
            repo_root=str(repo_root),
            entry=entry,
            outcome="added",
        )
        return True

    # No managed section — append it at end of file
    # Keep a blank separator if the file is non-empty and doesn't end with blank
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(GITIGNORE_BEGIN)
    lines.append(entry)
    lines.append(GITIGNORE_END)
    _write_content(gitignore_path, _rebuild(lines))
    log.debug(
        "gitignore_section_created",
        repo_root=str(repo_root),
        entry=entry,
        outcome="section_created",
    )
    return True


def remove_gitignore_entry(repo_root: Path, entry: str) -> bool:
    """Remove *entry* from the managed section.

    The managed section itself is preserved even when empty (until
    ``channel-doctor clean`` removes it explicitly).  Content outside the
    managed section is byte-identical.

    Args:
        repo_root: Repository root directory.
        entry: Line to remove.

    Returns:
        True if the entry was removed; False if it was not present.
    """
    gitignore_path = repo_root / ".gitignore"
    content = _read_content(gitignore_path)
    lines = content.splitlines()

    section = _get_managed_section(content)
    if section is None:
        return False

    begin_idx, end_idx = section
    interior = lines[begin_idx + 1 : end_idx]
    if entry not in interior:
        return False

    interior.remove(entry)
    new_lines = lines[: begin_idx + 1] + interior + lines[end_idx:]
    _write_content(gitignore_path, _rebuild(new_lines))
    log.debug(
        "gitignore_entry_removed",
        repo_root=str(repo_root),
        entry=entry,
        outcome="removed",
    )
    return True


def list_gitignore_entries(repo_root: Path) -> list[str]:
    """Return entries inside the managed section.

    Args:
        repo_root: Repository root directory.

    Returns:
        List of entry strings (preserving order). Empty list when the managed
        section is absent or the file does not exist.
    """
    gitignore_path = repo_root / ".gitignore"
    content = _read_content(gitignore_path)
    lines = content.splitlines()
    section = _get_managed_section(content)
    if section is None:
        return []
    begin_idx, end_idx = section
    return [ln for ln in lines[begin_idx + 1 : end_idx] if ln.strip()]
