"""Runtime memory-store pressure helpers.

These helpers are intentionally lightweight and fail-open. They inspect the
writer registry sidecar files used by trw-memory without opening SQLite, so
session-start can decide whether best-effort writes should be deferred before it
risks waiting on SQLite's busy timeout.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
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


def _parse_heartbeat_ts(value: object) -> datetime | None:
    """Parse a pins.json ``last_heartbeat_ts`` string into an aware UTC datetime.

    Accepts the ``_iso_now`` format (``...%f`` + trailing ``Z``) as well as
    plain ISO8601.  Returns ``None`` on any malformed value so the heartbeat
    filter fails open (the PID stays counted rather than being dropped on a
    parse error).
    """

    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    candidate = raw.removesuffix("Z")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stale_heartbeat_pids(trw_dir: Path, pin_ttl_hours: int) -> set[int]:
    """Return PIDs whose freshest pins.json heartbeat is older than the TTL.

    Builds a ``pid -> freshest last_heartbeat_ts`` map from
    ``.trw/runtime/pins.json`` and returns the set of PIDs whose freshest
    heartbeat age exceeds ``pin_ttl_hours``.  Fail-open: any error returns an
    empty set (no PID is dropped) and a PID with no pin entry is simply absent
    from the map, so callers must treat "not in the map" as "keep counted".
    """

    if pin_ttl_hours <= 0:
        return set()
    try:
        from trw_mcp.state._pin_store import load_pin_store

        store = load_pin_store()
    except Exception:  # justified: heartbeat-age filtering is advisory and fail-open
        logger.debug("writer_heartbeat_pins_load_failed", exc_info=True)
        return set()

    freshest: dict[int, datetime] = {}
    for entry in store.values():
        if not isinstance(entry, dict):
            continue
        pid_raw = entry.get("pid")
        if not isinstance(pid_raw, int):
            continue
        hb = _parse_heartbeat_ts(entry.get("last_heartbeat_ts"))
        if hb is None:
            continue
        current = freshest.get(pid_raw)
        if current is None or hb > current:
            freshest[pid_raw] = hb

    if not freshest:
        return set()

    cutoff = datetime.now(timezone.utc).timestamp() - pin_ttl_hours * 3600
    stale: set[int] = set()
    for pid, hb in freshest.items():
        if hb.timestamp() < cutoff:
            stale.add(pid)
    return stale


def live_memory_writer_pids(trw_dir: Path, *, pin_ttl_hours: int | None = None) -> list[int]:
    """Return sorted live writer PIDs from ``memory.db.writers/*.lock``.

    Stale or malformed locks are ignored but logged at DEBUG so diagnostics can
    distinguish "no pressure" from "registry unreadable" without perturbing the
    hot path.

    When ``pin_ttl_hours`` is provided (F5 root-cause A), a PID is ALSO dropped
    when its freshest ``.trw/runtime/pins.json`` heartbeat is older than
    ``pin_ttl_hours`` — an abandoned-but-alive ceremony session whose process
    lingers would otherwise permanently count as a writer and starve the
    deferrable backfill paths.  Fail-open: a PID with no pin heartbeat entry
    (likely a non-ceremony writer) stays counted.
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

    if pin_ttl_hours is not None and pids:
        stale = _stale_heartbeat_pids(trw_dir, pin_ttl_hours)
        for pid in sorted(pids & stale):
            logger.debug("writer_heartbeat_stale_ignored", pid=pid, pin_ttl_hours=pin_ttl_hours)
        pids -= stale
    return sorted(pids)


def should_defer_memory_side_effects(
    trw_dir: Path, *, threshold: int, pin_ttl_hours: int | None = None
) -> tuple[bool, list[int]]:
    """Return whether best-effort SQLite side effects should be deferred."""

    if threshold <= 1:
        threshold = 2
    pids = live_memory_writer_pids(trw_dir, pin_ttl_hours=pin_ttl_hours)
    return len(pids) >= threshold, pids


def should_defer_session_start_optional_work(
    trw_dir: Path,
    *,
    threshold: int,
    pin_ttl_hours: int | None = None,
) -> tuple[bool, list[int], str]:
    """Return whether optional session-start work should be skipped/deferred.

    Pressure is measured against PEER writers — pids registered in the writer
    registry that are NOT the calling process. Self-only registration is the
    normal steady state for both:

    * stdio per-instance MCP servers (one writer per process), and
    * the shared HTTP MCP server (one long-lived process owns the backend).

    Treating self-only as "writer_present" pressure (the previous behavior)
    caused 100% of session-start calls in the shared HTTP server to defer
    every optional maintenance step — auto-upgrade check, stale-run close,
    embeddings backfill, and WAL checkpoint — turning the deferral path into
    a permanent skip and letting the WAL grow unbounded.
    """

    if threshold <= 1:
        threshold = 2
    pids = live_memory_writer_pids(trw_dir, pin_ttl_hours=pin_ttl_hours)
    peer_pids = [pid for pid in pids if pid != os.getpid()]
    # Self-only registration is the normal steady state — never defers.
    if not peer_pids:
        return False, pids, ""
    # ``threshold`` counts total writers (including self), matching the
    # ``should_defer_memory_side_effects`` calibration.
    if len(pids) >= threshold:
        return True, pids, "writer_pressure"
    return True, pids, "writer_present"
