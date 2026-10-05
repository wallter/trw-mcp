"""Doctor row: a root ``CLAUDE.md`` that hides ``AGENTS.md`` from Claude Code.

Belongs to the ``_subcommands_doctor.py`` facade (kept out of that file for its
module-size gate). Claude Code reads ``AGENTS.md`` only when no ``CLAUDE.md``
exists, so in a claude-code project a root ``CLAUDE.md`` masks the TRW block in
``AGENTS.md`` unless it reaches TRW's context itself. ``update-project`` keeps a
small marked TRW block in an existing ``CLAUDE.md`` that imports
``.trw/INSTRUCTIONS.md`` (operator P0, 2026-10-01), so the row passes when that
block is there, or when the file imports (or links to) ``AGENTS.md``; otherwise
it says to run ``update-project``. It never advises deleting the file.

The row only ever reads ``CLAUDE.md`` itself: a symlink is judged by where it
points, never followed for content, and a regular file is opened non-blocking,
without following links, and read up to :data:`_MAX_READ` bytes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, cast

from trw_mcp.models.config import TRWConfig

if TYPE_CHECKING:
    from trw_mcp.server._subcommands_doctor import CheckResult, DoctorStatus

#: A TRW-only leftover is a few hundred bytes; anything larger is the user's file.
_MAX_READ = 1 << 20
_MASKS = "Claude Code skips AGENTS.md while it exists"


def _imports_agents_md(text: str) -> bool:
    """True when *text* imports AGENTS.md the way Claude Code reads it (code spans and fences are inert)."""
    from trw_mcp.state.claude_md._instructions_link import live_imports

    return any(token in ("AGENTS.md", "./AGENTS.md") for token in live_imports(text))


def _symlink_row(target: Path, path: Path) -> tuple[str, str]:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return "WARN", f"{path} is a broken or looping symlink ({exc}); {_MASKS}"
    if resolved == (target / "AGENTS.md").resolve():
        return "PASS", "CLAUDE.md links to AGENTS.md"
    return "WARN", f"{path} links to {os.readlink(path)}, not AGENTS.md; {_MASKS}"


def _read_regular(path: Path) -> bytes | str:
    """Up to ``_MAX_READ + 1`` bytes of *path*, or a reason it is not a readable regular file.

    ``O_NONBLOCK`` and ``O_NOFOLLOW`` are POSIX-only; where absent (Windows) the flag is simply not set.
    The descriptor is closed on every path.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return f"cannot be read ({exc})"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "is not a regular file"
        chunks: list[bytes] = []
        remaining = _MAX_READ + 1
        while remaining > 0 and (chunk := os.read(fd, remaining)):
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError as exc:
        return f"cannot be read ({exc})"
    finally:
        os.close(fd)


def claude_md_masks_agents_md_row(target: Path) -> tuple[str, str]:
    """``(status, message)``: WARN when a root ``CLAUDE.md`` hides ``AGENTS.md`` from Claude Code."""
    from trw_mcp.bootstrap._template_claude_md import claude_code_is_claimed

    path = target / "CLAUDE.md"
    if not os.path.lexists(path):
        return "PASS", "no root CLAUDE.md"
    if not claude_code_is_claimed(target):
        return "SKIP", "claude-code is not a client of this project"
    if path.is_symlink():
        return _symlink_row(target, path)
    raw = _read_regular(path)
    if isinstance(raw, str):
        return "WARN", f"{path} {raw}; {_MASKS}"
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if len(raw) > _MAX_READ:
        # The read stopped mid-file: drop the partial last line so a cut token (``@AGENTS.md.evil``) never
        # reads as a whole one.
        text = text[: text.rfind("\n") + 1]
    if _carries_trw_block(text):
        return "PASS", "CLAUDE.md carries TRW's block, which imports .trw/INSTRUCTIONS.md"
    if _imports_agents_md(text):
        return "PASS", "CLAUDE.md imports AGENTS.md"
    return (
        "WARN",
        f"{path} carries no current TRW block, and {_MASKS}, so Claude Code does not load TRW's instructions: "
        "run `trw-mcp update-project` to add TRW's block (it imports .trw/INSTRUCTIONS.md; "
        "nothing outside its markers changes).",
    )


def _carries_trw_block(text: str) -> bool:
    """True when *text* holds exactly one whole-line trw:start..trw:end block with the INSTRUCTIONS import inside."""
    from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH, fenced_line_indices
    from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

    fenced = fenced_line_indices(text)
    lines = [line.strip() if i not in fenced else "" for i, line in enumerate(text.splitlines())]
    if lines.count(TRW_MARKER_START) != 1 or lines.count(TRW_MARKER_END) != 1:
        return False
    start, end = lines.index(TRW_MARKER_START), lines.index(TRW_MARKER_END)
    return start < end and f"@{INSTRUCTIONS_RELPATH}" in lines[start + 1 : end]


def check_claude_md_masks_agents_md(target: Path, _config: TRWConfig) -> CheckResult:
    """WARN when a root ``CLAUDE.md`` hides ``AGENTS.md`` from Claude Code; never touches the file."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    status, message = claude_md_masks_agents_md_row(target)
    return CheckResult("claude_md_masks_agents_md", cast("DoctorStatus", status), message)


__all__ = ["check_claude_md_masks_agents_md", "claude_md_masks_agents_md_row"]
