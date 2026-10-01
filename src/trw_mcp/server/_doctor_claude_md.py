"""Doctor row: a root ``CLAUDE.md`` that hides ``AGENTS.md`` from Claude Code.

Belongs to the ``_subcommands_doctor.py`` facade (kept out of that file for its
module-size gate). Claude Code reads ``AGENTS.md`` only when no ``CLAUDE.md``
exists, so in a claude-code project any other root ``CLAUDE.md`` masks the TRW
protocol TRW writes to ``AGENTS.md``. TRW never edits or removes that file; this
row says how to fix it. A ``CLAUDE.md`` that imports ``AGENTS.md`` (or links to
it) is the fix itself, so it passes (FB-INSTALL-02).

The row only ever reads ``CLAUDE.md`` itself: a symlink is judged by where it
points, never followed for content, and a regular file is opened non-blocking,
without following links, and read up to :data:`_MAX_READ` bytes.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, cast

from trw_mcp.models.config import TRWConfig

if TYPE_CHECKING:
    from trw_mcp.server._subcommands_doctor import CheckResult, DoctorStatus

#: A TRW-only leftover is a few hundred bytes; anything larger is the user's file.
_MAX_READ = 1 << 20
_MASKS = "Claude Code skips AGENTS.md while it exists"
#: Claude Code's import syntax: an ``@path`` token, not inside a code span or fenced block.
_IMPORT = re.compile(r"(?:^|\s)@(?:\./)?AGENTS\.md(?=\s|$)", re.MULTILINE)
#: CommonMark: a fence is 3+ backticks or tildes indented at most 3 spaces; it closes on a run of the
#: same character at least as long, with nothing after it but whitespace.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
#: CommonMark code span: a run of N backticks closed by the next run of exactly N.
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")


def _imports_agents_md(text: str) -> bool:
    """True when *text* imports AGENTS.md the way Claude Code reads it (code spans and fences are inert)."""
    live: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)
            continue
        if fence is not None:
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= len(fence)
                and not match.group(2).strip()
            ):
                fence = None
            continue
        live.append(_CODE_SPAN.sub("", line))
    return bool(_IMPORT.search("\n".join(live)))


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
    if _imports_agents_md(text):
        return "PASS", "CLAUDE.md imports AGENTS.md"
    from trw_mcp.state.claude_md._orphan_strip import _is_trw_only, _strip_trw_section

    if len(raw) <= _MAX_READ and _is_trw_only(_strip_trw_section(text)[1]):
        return (
            "WARN",
            f"{path} holds only TRW content left by a pre-8.0 install, and {_MASKS}: "
            "delete it (TRW 8.0 writes its protocol to AGENTS.md).",
        )
    return (
        "WARN",
        f"{path} has user content. TRW 8.0 writes its protocol to AGENTS.md; "
        "Claude Code skips AGENTS.md while a CLAUDE.md exists, so add an `@AGENTS.md` line to it "
        "(and delete any old TRW block between the trw:start/trw:end markers).",
    )


def check_claude_md_masks_agents_md(target: Path, _config: TRWConfig) -> CheckResult:
    """WARN when a root ``CLAUDE.md`` hides ``AGENTS.md`` from Claude Code; never touches the file."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    status, message = claude_md_masks_agents_md_row(target)
    return CheckResult("claude_md_masks_agents_md", cast("DoctorStatus", status), message)


__all__ = ["check_claude_md_masks_agents_md", "claude_md_masks_agents_md_row"]
