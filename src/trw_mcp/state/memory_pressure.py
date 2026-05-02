"""Runtime memory-store pressure helpers.

These helpers are intentionally lightweight and fail-open. They inspect the
writer registry sidecar files used by trw-memory without opening SQLite, so
session-start can decide whether best-effort writes should be deferred before it
risks waiting on SQLite's busy timeout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _pid_is_alive(pid: int) -> bool:
    """Return True if *pid* appears to name a live local process."""

    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError:
            logger.debug("writer_pid_check_skipped_no_psutil", pid=pid)
            return True
        return bool(psutil.pid_exists(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _read_writer_pid(lock_path: Path) -> int | None:
    """Parse the first line of a writer lock file as a PID."""

    try:
        first_line = lock_path.read_text(encoding="utf-8").splitlines()[0]
    except (IndexError, OSError, UnicodeDecodeError):
        return None
    try:
        return int(first_line.strip())
    except ValueError:
        return None


def live_memory_writer_pids(trw_dir: Path) -> list[int]:
    """Return sorted live writer PIDs from ``memory.db.writers/*.lock``.

    Stale or malformed locks are ignored but logged at DEBUG so diagnostics can
    distinguish "no pressure" from "registry unreadable" without perturbing the
    hot path.
    """

    writers_dir = trw_dir / "memory" / "memory.db.writers"
    if not writers_dir.exists():
        return []

    pids: set[int] = set()
    try:
        lock_paths = list(writers_dir.glob("*.lock"))
    except OSError:
        logger.debug("writer_registry_scan_failed", path=str(writers_dir), exc_info=True)
        return []

    for lock_path in lock_paths:
        pid = _read_writer_pid(lock_path)
        if pid is None:
            logger.debug("writer_registry_lock_malformed", path=str(lock_path))
            continue
        if _pid_is_alive(pid):
            pids.add(pid)
        else:
            logger.debug("writer_registry_lock_stale_ignored", path=str(lock_path), pid=pid)
    return sorted(pids)


def should_defer_memory_side_effects(trw_dir: Path, *, threshold: int) -> tuple[bool, list[int]]:
    """Return whether best-effort SQLite side effects should be deferred."""

    if threshold <= 1:
        threshold = 2
    pids = live_memory_writer_pids(trw_dir)
    return len(pids) >= threshold, pids


def should_defer_session_start_optional_work(
    trw_dir: Path,
    *,
    threshold: int,
) -> tuple[bool, list[int], str]:
    """Return whether optional session-start work should be skipped/deferred.

    ``should_defer_memory_side_effects`` intentionally models cross-process
    writer pressure and therefore keeps the default threshold at two writers.
    The session-start hot path has a stricter requirement: after the first
    SQLite recall opens the local backend, even this process's own writer
    registration is enough signal that nonessential receipt logs, maintenance,
    and nudge decoration should not sit on the response path.
    """

    if threshold <= 1:
        threshold = 2
    pids = live_memory_writer_pids(trw_dir)
    if len(pids) >= threshold:
        return True, pids, "writer_pressure"
    if pids:
        return True, pids, "writer_present"
    return False, pids, ""
