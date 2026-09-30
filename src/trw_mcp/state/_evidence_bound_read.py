"""Fail-closed reads of evidence artifacts a blocking gate decides on (E2E-EVIDENCE-BOUND-READ).

One implementation of the read two deliver gates had hand-rolled, and got wrong the same two ways
(E2E-INC-112): a symlink swapped in after the check was followed, and a ``null`` document read as an
empty mapping. :meth:`trw_mcp.state.persistence.FileStateReader.read_yaml` does both by design, so it is
not usable where an unreadable artifact must count AGAINST the delivery.

The artifact is classified with ``lstat`` (only ``FileNotFoundError`` means absent), must be a regular
file, and is read through :func:`trw_mcp._checkout_access.open_under` (no component below the anchor may be
a symlink) on a descriptor whose ``(st_dev, st_ino)`` matches what ``lstat`` classified. The raw text is
parsed with the safe loader, and anything but a mapping is unreadable.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from trw_mcp._checkout_access import open_under
from trw_mcp._locking import _lock_sh, _lock_un
from trw_mcp.state._persistence_helpers import _safe_yaml


class EvidenceUnreadable(Exception):
    """The artifact exists but is not a readable mapping: the gate's verdict is UNKNOWN, never absent."""


def read_evidence_mapping(anchor: Path, relative: str, *, empty_is_absent: bool = False) -> dict[str, object] | None:
    """The artifact at ``anchor/relative`` as a mapping; ``None`` only when it is truly absent.

    ``empty_is_absent``: an existing but empty or whitespace-only file also returns ``None`` -- a gate
    whose policy treats an emptied artifact like a deleted one opts in explicitly. Raises
    :class:`EvidenceUnreadable` for every other state (unstatable, not a regular file, replaced between
    classification and read, undecodable, invalid YAML, a non-mapping root including ``null``, or any other
    failure to examine or read it -- only the caller's own bugs and BaseException escape).
    """
    try:
        before = os.lstat(anchor / relative)
    except FileNotFoundError:  # trw-fail-silent-allow: truly absent; the caller's absence policy applies
        return None
    except Exception as exc:  # justified: any other failure to examine it (EACCES, a malformed path) is unreadable
        raise EvidenceUnreadable(f"{relative} cannot be examined") from exc
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceUnreadable(f"{relative} is not a regular file")
    try:
        text = _read_bound_text(anchor, relative, before)
    except Exception as exc:  # justified: EVERY read failure is unreadable, never an escape (codex r1 KI)
        raise EvidenceUnreadable(f"{relative} could not be read") from exc
    if empty_is_absent and not text.strip():
        return None
    try:
        data = _safe_yaml().load(text)
    except Exception as exc:  # justified: every parser failure is the same answer -- unreadable, raised
        raise EvidenceUnreadable(f"{relative} is not valid YAML") from exc
    if not isinstance(data, dict):
        raise EvidenceUnreadable(f"{relative} root must be a mapping, got {type(data).__name__}")
    return data


def read_evidence_text(anchor: Path, relative: str) -> str | None:
    """The raw text at ``anchor/relative``; ``None`` only when truly absent, :class:`EvidenceUnreadable` otherwise.

    The same classification and bound read as :func:`read_evidence_mapping`, for a non-YAML artifact (the
    append-only ``events.jsonl`` witness) whose caller parses it itself.
    """
    try:
        before = os.lstat(anchor / relative)
    except FileNotFoundError:  # trw-fail-silent-allow: truly absent; the caller's absence policy applies
        return None
    except Exception as exc:  # justified: any other failure to examine it is unreadable
        raise EvidenceUnreadable(f"{relative} cannot be examined") from exc
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceUnreadable(f"{relative} is not a regular file")
    try:
        return _read_bound_text(anchor, relative, before)
    except Exception as exc:  # justified: EVERY read failure is unreadable, never an escape
        raise EvidenceUnreadable(f"{relative} could not be read") from exc


def _read_bound_text(anchor: Path, relative: str, before: os.stat_result) -> str:
    fd = open_under(anchor, relative)
    try:
        now = os.fstat(fd)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino):
            raise OSError(f"{relative} changed between classification and read")
        handle = os.fdopen(fd, "r", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        _lock_sh(handle.fileno())
        try:
            return handle.read()
        finally:
            _lock_un(handle.fileno())
