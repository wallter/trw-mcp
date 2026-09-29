"""Contained, byte-bounded reads for the factory status reader (PRD-CORE-340 FR03/NFR02).

Belongs to the ``_factory_status.py`` facade. Every open goes through
:func:`trw_mcp._checkout_access.open_under` and every read is capped by the evidence substrate's
:class:`~trw_mcp.models._evidence_core.EvidenceLimits`. A refusal is a state, never an exception.

Threat model: the caller-supplied ANCHOR (a run directory) is opened following symlinks, so a symlinked
``.trw/runs`` or run directory the operator chose is allowed. Every component BELOW the anchor is opened
``O_NOFOLLOW`` from its parent's directory fd, so a symlinked leaf or parent inside a run is refused
(``path_escape``); only a non-root local user who can already write the run tree is the adversary, and
this contains what a receipt reference or journal can make the reader open, not what that user can write.
Non-regular files (directory, FIFO) are ``unreadable``. POSIX only (macOS/Linux, as trw-mcp is). Callers must not skip their own containment
check on a cross-run ``owner``: this module contains the tree below an anchor, not the choice of anchor.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from trw_mcp._checkout_access import open_under
from trw_mcp.models._evidence_core import EvidenceLimits

__all__ = ["LINE_MAX", "LONG_LINE", "STREAM_MAX", "TRUNCATED", "iter_lines", "read_bounded", "refusal_state"]

LINE_MAX = EvidenceLimits.MAX_CANONICAL_RECEIPT_BYTES  # one journal line
STREAM_MAX = EvidenceLimits.MAX_BOUND_FILE_BYTES  # one whole journal
LONG_LINE = object()  # yielded in place of one line longer than LINE_MAX
TRUNCATED = object()  # yielded once, last, when the journal exceeds STREAM_MAX


def _has_symlink_component(anchor: Path, relative: str) -> bool:
    current = anchor
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def refusal_state(exc: OSError, anchor: Path, relative: str) -> str:
    """Map an open/read failure to ``missing`` | ``path_escape`` | ``unreadable``.

    ``ELOOP`` is the ``O_NOFOLLOW`` refusal. ``ENOTDIR`` is ambiguous (Linux reports a symlinked directory
    that way; it is also a plain "a parent is a file"), so it is a ``path_escape`` only if a component is
    a symlink.
    """
    if isinstance(exc, FileNotFoundError):
        return "missing"
    if exc.errno == errno.ELOOP or (exc.errno == errno.ENOTDIR and _has_symlink_component(anchor, relative)):
        return "path_escape"
    return "unreadable"


def _open_regular(anchor: Path, relative: str) -> int:
    fd = open_under(anchor, relative)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PermissionError(errno.EACCES, "not a regular file")
    return fd


def read_bounded(anchor: Path, relative: str, limit: int) -> tuple[bytes | None, str]:
    """Return ``(bytes, "ok")`` or ``(None, state)``; state is missing/path_escape/unreadable/oversize."""
    try:
        fd = _open_regular(anchor, relative)
    except OSError as exc:
        return None, refusal_state(exc, anchor, relative)
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None, "unreadable"
    return (None, "oversize") if len(data) > limit else (data, "ok")


def iter_lines(anchor: Path, relative: str) -> Iterator[bytes | object]:
    """Stream lines with a per-line cap (:data:`LINE_MAX`, newline excluded) and a journal cap (:data:`STREAM_MAX`).

    A line longer than the cap yields :data:`LONG_LINE` (the rest of it is drained, never held). The running
    total is checked BEFORE a line is yielded; past the journal cap a single :data:`TRUNCATED` ends the
    stream. Open failures raise ``OSError`` before any yield.
    """
    with os.fdopen(_open_regular(anchor, relative), "rb") as handle:
        total = 0
        while raw := handle.readline(LINE_MAX + 2):  # room for LINE_MAX bytes plus "\n"
            total += len(raw)
            drained = raw.endswith(b"\n")
            while not drained and len(raw) >= LINE_MAX + 2 and (tail := handle.readline(LINE_MAX + 2)):
                total += len(tail)
                drained = tail.endswith(b"\n")
                if total > STREAM_MAX:  # a run of huge lines still trips the journal cap while draining
                    break
            if total > STREAM_MAX:
                yield TRUNCATED
                return
            long = len(raw) - raw.endswith(b"\n") > LINE_MAX
            yield LONG_LINE if long else raw
