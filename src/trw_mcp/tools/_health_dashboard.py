"""Session-start memory health dashboard (PRD-INFRA-068 / C3).

Computes a small dict summarising the health of the local memory store:
snapshot age, integrity state, concurrent-writer count, and corrupt-backup
presence. Surfaced by ``trw_session_start`` so degradation is visible BEFORE
the next incident rather than after.

Fail-open: every individual field degrades to ``None`` or ``False`` when its
upstream feature is disabled or unavailable. An exception during computation
returns an empty dict — it never blocks session start.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import TypedDict

import structlog

__all__ = ["MemoryHealthDict", "compute_memory_health"]

logger = structlog.get_logger(__name__)

# Pattern copied from trw-memory/storage/sqlite_backend.py — keep in sync.
# Matches both timestamped (PRD-CORE-139) and legacy .corrupt.bak rotations.
_CORRUPT_BAK_RE: re.Pattern[str] = re.compile(r"^memory\.db\.corrupt\..*\.bak$")
_LEGACY_CORRUPT_NAMES: frozenset[str] = frozenset(
    {"memory.db.corrupt.bak", "memory.db.corrupt.bak.1"}
)
# Match snapshot filenames YYYY-MM-DD.db and YYYY-Www.db.
_SNAPSHOT_RE: re.Pattern[str] = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})\.db$")


class MemoryHealthDict(TypedDict, total=False):
    """Shape of the ``memory_health`` response key in :func:`trw_session_start`.

    All fields are optional — callers MUST use ``.get(key)`` semantics. A
    missing field means the upstream feature is disabled, unavailable, or
    the probe failed silently.
    """

    integrity_ok: bool
    corrupt_bak_present: bool
    corrupt_bak_count: int
    concurrent_writers: int
    last_snapshot_age_hours: int | None
    last_integrity_check_age_minutes: int | None
    db_path: str


def compute_memory_health(trw_dir: Path) -> MemoryHealthDict:
    """Produce the memory-health dict for :func:`trw_session_start`.

    Each field is computed in its own try/except so that one probe's
    failure cannot drop the others. Every field has a safe default.

    Args:
        trw_dir: Resolved ``.trw`` directory.

    Returns:
        :class:`MemoryHealthDict` populated with whatever can be observed.
    """
    db_path = trw_dir / "memory" / "memory.db"
    result: MemoryHealthDict = {"db_path": str(db_path)}

    # Integrity probe (PRD-INFRA-068 FR01).
    try:
        result["integrity_ok"] = _probe_integrity(db_path)
    except Exception:  # justified: fail-open per-field (FR02)
        logger.debug("health_dashboard_failed", field="integrity_ok", exc_info=True)
        result["integrity_ok"] = True  # safe default — assume healthy if probe crashes

    # Corrupt backup presence (PRD-CORE-139 forensic evidence surface).
    try:
        corrupt_count = _count_corrupt_backups(db_path.parent)
        result["corrupt_bak_count"] = corrupt_count
        result["corrupt_bak_present"] = corrupt_count > 0
    except Exception:
        logger.debug("health_dashboard_failed", field="corrupt_bak", exc_info=True)
        result["corrupt_bak_count"] = 0
        result["corrupt_bak_present"] = False

    # Writer-registry peer count (PRD-INFRA-064 B3).
    try:
        result["concurrent_writers"] = _count_live_writers(db_path)
    except Exception:
        logger.debug("health_dashboard_failed", field="concurrent_writers", exc_info=True)
        result["concurrent_writers"] = 0

    # Snapshot age (PRD-INFRA-065 B4). None when snapshots are disabled or absent.
    try:
        result["last_snapshot_age_hours"] = _most_recent_snapshot_age_hours(trw_dir)
    except Exception:
        logger.debug("health_dashboard_failed", field="last_snapshot_age_hours", exc_info=True)
        result["last_snapshot_age_hours"] = None

    # Integrity-scheduler age (PRD-INFRA-063 B2). None when disabled.
    try:
        result["last_integrity_check_age_minutes"] = _integrity_scheduler_age_minutes(trw_dir)
    except Exception:
        logger.debug(
            "health_dashboard_failed",
            field="last_integrity_check_age_minutes",
            exc_info=True,
        )
        result["last_integrity_check_age_minutes"] = None

    return result


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


def _probe_integrity(db_path: Path) -> bool:
    """Run one fresh PRAGMA quick_check. Returns True when missing (no regression)."""
    if not db_path.exists():
        return True
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
            return len(rows) == 1 and rows[0][0] == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    except Exception:  # justified: fail-open
        return True


def _count_corrupt_backups(mem_dir: Path) -> int:
    """Count files matching the corrupt-backup naming pattern."""
    if not mem_dir.exists() or not mem_dir.is_dir():
        return 0
    try:
        return sum(
            1
            for f in mem_dir.iterdir()
            if f.is_file()
            and (_CORRUPT_BAK_RE.fullmatch(f.name) or f.name in _LEGACY_CORRUPT_NAMES)
        )
    except OSError:
        return 0


def _count_live_writers(db_path: Path) -> int:
    """Count live entries in the PRD-INFRA-064 writer registry."""
    registry_dir = db_path.parent / f"{db_path.name}.writers"
    if not registry_dir.exists() or not registry_dir.is_dir():
        return 0
    try:
        lock_files = list(registry_dir.glob("*.lock"))
    except OSError:
        return 0

    live = 0
    for lf in lock_files:
        pid = _parse_pid(lf.name)
        if pid is None:
            continue
        if _pid_is_live(pid):
            live += 1
    return live


def _most_recent_snapshot_age_hours(trw_dir: Path) -> int | None:
    """Return the age in hours of the newest daily snapshot, or None if absent."""
    daily_dir = trw_dir / "memory" / "snapshots" / "daily"
    if not daily_dir.exists():
        return None
    try:
        parseable = [
            f for f in daily_dir.iterdir() if f.is_file() and _SNAPSHOT_RE.fullmatch(f.name)
        ]
    except OSError:
        return None
    if not parseable:
        return None
    newest = max(parseable, key=lambda p: p.name)
    try:
        mtime = newest.stat().st_mtime
    except OSError:
        return None
    age_seconds = max(0.0, time.time() - mtime)
    return int(age_seconds // 3600)


def _integrity_scheduler_age_minutes(trw_dir: Path) -> int | None:
    """Return minutes since the last integrity probe sentinel.

    The background scheduler in ``trw_memory.storage._integrity_scheduler``
    stores ``last_check_at`` on an instance, not on disk. At session_start we
    don't have access to that instance, so instead we read an optional
    sentinel file the scheduler can drop. Absence of the sentinel → None.
    """
    sentinel = trw_dir / "memory" / ".integrity_last_check"
    if not sentinel.exists():
        return None
    try:
        content = sentinel.read_text().strip()
        last_ts = float(content)
    except (OSError, ValueError):
        return None
    age_seconds = max(0.0, time.time() - last_ts)
    return int(age_seconds // 60)


# ---------------------------------------------------------------------------
# Helpers (duplicated from trw_memory/storage/_writer_registry for
# independence — keep in sync if the registry format changes)
# ---------------------------------------------------------------------------


def _parse_pid(name: str) -> int | None:
    if not name.endswith(".lock"):
        return None
    stem = name[: -len(".lock")]
    try:
        pid = int(stem)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_is_live(pid: int) -> bool:
    if os.name != "posix":
        # Non-POSIX — be conservative, assume live.
        return True
    # On Linux, /proc/<pid> exists iff the pid is live.
    proc = Path(f"/proc/{pid}")
    if proc.parent.exists():
        return proc.exists()
    # Fallback to signal-0 probe on non-Linux POSIX (macOS/BSD).
    try:
        os.kill(pid, 0)
    except OSError as exc:
        import errno

        return exc.errno == errno.EPERM
    return True
