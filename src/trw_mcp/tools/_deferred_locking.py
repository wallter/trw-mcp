"""Deferred-delivery file-lock acquisition and stale-holder recovery."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex_nb, _lock_un

logger = structlog.get_logger(__name__)


def _peek_deferred_lock_holder(lock_path: Path) -> dict[str, object] | None:
    """Read the latest JSON pid+timestamp record written by the lock holder."""
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_lock_record_stale(record: dict[str, object], max_age_seconds: float) -> bool:
    """Return True when the recorded holder is gone or beyond the age budget."""
    pid_field = record.get("pid")
    if isinstance(pid_field, int):
        try:
            os.kill(pid_field, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass

    ts_field = record.get("ts")
    if isinstance(ts_field, str):
        try:
            record_ts = datetime.fromisoformat(ts_field.replace("Z", "+00:00"))
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - record_ts).total_seconds() > max_age_seconds
    return False


def _write_lock_record(
    fd: io.TextIOWrapper,
    *,
    reclaimed_from: dict[str, object] | None = None,
) -> None:
    record: dict[str, object] = {"pid": os.getpid(), "ts": datetime.now(timezone.utc).isoformat()}
    if reclaimed_from is not None:
        record["reclaimed_from"] = reclaimed_from
    fd.seek(0)
    fd.truncate()
    fd.write(json.dumps(record) + "\n")
    fd.flush()


def _try_acquire_deferred_lock(
    trw_dir: Path,
    *,
    stale_threshold_seconds: float = 600.0,
) -> io.TextIOWrapper | None:
    """Try to acquire the deferred-deliver file lock (non-blocking)."""
    lock_path = trw_dir / "deliver-deferred.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("a+", encoding="utf-8")
    try:
        _lock_ex_nb(fd.fileno())
        _write_lock_record(fd)
        return fd
    except Exception:  # justified: cleanup, lock acquisition failure returns None
        fd.close()

    record = _peek_deferred_lock_holder(lock_path)
    if record is None or not _is_lock_record_stale(record, stale_threshold_seconds):
        return None

    logger.warning(
        "deferred_lock_reclaimed_stale",
        holder=record,
        stale_threshold_seconds=stale_threshold_seconds,
    )
    fd2: io.TextIOWrapper | None = None
    try:
        fd2 = lock_path.open("a+", encoding="utf-8")
        _lock_ex_nb(fd2.fileno())
        _write_lock_record(fd2, reclaimed_from=record)
        return fd2
    except Exception:  # justified: stale-reclaim failure must not raise into deliver path
        logger.debug("deferred_lock_reclaim_failed", exc_info=True)
        if fd2 is not None:
            fd2.close()
    return None


def _release_deferred_lock(fd: object) -> None:
    """Release the deferred-deliver file lock."""
    try:
        if isinstance(fd, io.TextIOWrapper):
            _lock_un(fd.fileno())
            fd.close()
    except Exception:  # justified: fail-open, lock release cleanup
        logger.debug("lock_release_failed", exc_info=True)
