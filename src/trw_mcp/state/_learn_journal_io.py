"""Durable JSON-record primitives for the learn write-ahead journal.

Belongs to the ``state/learn_journal.py`` facade. Three call sites need the same
crash-safe write — the journal append, the drain's retry-attempt bookkeeping,
and the dead-letter move — so the atomic ``tmp`` + :func:`os.replace` + ``fsync``
sequence lives here exactly once rather than being reimplemented (and drifting)
per call site.

Every helper is FAIL-OPEN and reports success as a bool: journaling must never
block a learn, and — the harder rule — a caller must never *believe* a record
moved when it did not. A ``False`` return is what lets the drain keep a record
on disk instead of booking a move that never happened.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Schema version for forward-compat: a drain that reads an unknown-shaped record
# skips (does not consume) it rather than replaying garbage.
JOURNAL_RECORD_VERSION = 1


def fsync_dir(directory: Path) -> None:
    """Best-effort fsync of a directory so a new/removed entry is durable.

    Directory fsync is a POSIX durability requirement for create/unlink to
    survive a crash; it is a no-op or unsupported on some platforms, so any
    failure is swallowed (the file fsync already covers data durability).
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:  # justified: fail-open, directory fsync is best-effort (unsupported on some FS)
        logger.debug("learn_journal_dir_fsync_unsupported", directory=str(directory))
    finally:
        os.close(fd)


def write_record_atomic(target: Path, record: dict[str, object], *, preserve_mtime: bool = False) -> bool:
    """Durably write *record* to *target*; return whether it landed.

    Args:
        target: Final record path. Parent directories are created as needed.
        record: JSON-serialisable record body.
        preserve_mtime: Restore the pre-existing mtime after the replace. The
            drain's retry-attempt bookkeeping REWRITES a pending record in
            place, and both the FIFO replay order and the age escape hatch
            (:func:`~trw_mcp.state.learn_journal.aged_pending_count`) are keyed
            on mtime — without this, every failed attempt would reset a
            record's age and the eventual-drain guarantee would never fire.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp: tuple[float, float] | None = None
        if preserve_mtime:
            try:
                st = target.stat()
                stamp = (st.st_atime, st.st_mtime)
            except OSError:  # justified: fail-open, an unstattable record simply keeps the new mtime
                stamp = None
        tmp = target.with_suffix(".json.tmp")
        data = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(target))
        if stamp is not None:
            os.utime(str(target), stamp)
        fsync_dir(target.parent)
        return True
    except (OSError, ValueError, TypeError):  # justified: fail-open, a broken journal must never block a learn
        logger.warning("learn_journal_record_write_failed", path=str(target), exc_info=True)
        return False


def read_record(path: Path) -> dict[str, object] | None:
    """Return the parsed record at *path*, or ``None`` when it is unreadable."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # justified: fail-open, a corrupt record is skipped, never replayed
        logger.warning("learn_journal_record_unreadable", path=str(path), exc_info=True)
        return None
    if not isinstance(record, dict):
        logger.warning("learn_journal_record_malformed", path=str(path))
        return None
    return record


__all__ = ["JOURNAL_RECORD_VERSION", "fsync_dir", "read_record", "write_record_atomic"]
