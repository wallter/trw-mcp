"""Runtime memory-store pressure census.

These helpers are intentionally lightweight and fail-open. They inspect the
writer registry sidecar files used by trw-memory without opening SQLite, so
session-start can decide whether best-effort writes should be deferred before it
risks waiting on SQLite's busy timeout.

PRD-CORE-257-FR01 replaced two differently calibrated predicates — one that
deferred as soon as ONE peer writer existed and let the threshold merely
relabel the deferral reason, and one that counted the caller itself — with a
single frozen :class:`WriterCensus`. ``under_pressure`` is now exactly
``peer_writer_count >= threshold``, so the tunable decides the outcome instead
of decorating it, and the census is taken once and threaded to its consumers.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from trw_mcp.state._writer_census_identity import (
    HeartbeatState,
    IdentityState,
    lock_identity,
    read_writer_lock,
    stale_heartbeat_pids,
)

# Re-exported for ``state/_pin_ttl.py``, which shares this module's
# heartbeat-parsing contract and must not grow a second copy of it.
from trw_mcp.state._writer_census_identity import _parse_heartbeat_ts as _parse_heartbeat_ts
from trw_mcp.state.deferral_ledger import DeferralDecision

logger = structlog.get_logger(__name__)

CensusState = Literal["measured", "unreadable"]
"""``measured`` = the registry was scanned; ``unreadable`` = the scan failed.

An absence of measurement is not a measurement of absence: an ``unreadable``
census reports zero counts for SHAPE stability only, and any consumer that reads
``under_pressure`` without reading ``census_state`` is reading an unsafe
default.
"""


@dataclass(frozen=True, slots=True)
class WriterCensus:
    """One measurement of the live writer registry, plus the pressure verdict."""

    writer_pids: tuple[int, ...]
    writer_count: int
    peer_writer_count: int
    threshold: int
    under_pressure: bool
    census_state: CensusState
    identity_state: IdentityState
    heartbeat_state: HeartbeatState


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
        # trw-fail-silent-allow: the idiomatic liveness check — ProcessLookupError IS "no such process"
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _iter_lock_paths(writers_dir: Path) -> list[Path]:
    """List the registry's lock files. Raises ``OSError`` when it cannot.

    Uses ``os.scandir`` directly rather than ``Path.glob``. On Python 3.12
    ``Path.glob`` SWALLOWS an ``OSError`` raised while scanning the directory
    (e.g. ``PermissionError``) and yields an empty result instead of
    propagating it, which let a genuinely unreadable registry report a
    healthy zero-writer census (PRD-CORE-257 audit row 4, confirmed with a
    direct ``_scandir`` probe returning ``[]`` for a ``PermissionError``).
    ``os.scandir`` opens the directory eagerly, so the ``OSError`` surfaces
    at the ``with`` statement, before any entry is read.
    """
    with os.scandir(writers_dir) as entries:
        return [Path(entry.path) for entry in entries if entry.name.endswith(".lock")]


def _scan_writer_locks(trw_dir: Path) -> tuple[list[int], CensusState, IdentityState]:
    """Scan the writer registry into live PIDs plus the two measurement states.

    Malformed and dead locks are ignored but logged at DEBUG so diagnostics can
    tell "no pressure" from "registry unreadable"; a PID-reuse ghost is excluded
    at WARNING because it is a correctness exclusion, not routine noise.
    """
    writers_dir = trw_dir / "memory" / "memory.db.writers"
    if not writers_dir.exists():
        # A registry that was never created is a measured zero, not a failure.
        return [], "measured", "verified"
    try:
        lock_paths = _iter_lock_paths(writers_dir)
    except OSError:
        logger.warning("writer_registry_scan_failed", path=str(writers_dir), exc_info=True)
        return [], "unreadable", "unverified"

    pids: set[int] = set()
    identity_state: IdentityState = "verified"
    for lock_path in lock_paths:
        try:
            record = read_writer_lock(lock_path)
        except (OSError, UnicodeDecodeError):
            # An unreadable lock is a measurement failure, not evidence of
            # absence (audit row 4): it must degrade identity_state, never
            # leave the census reporting "verified" over an undercount.
            logger.warning("writer_registry_lock_unreadable", path=str(lock_path), exc_info=True)
            identity_state = "unverified"
            continue
        if record is None:
            logger.debug("writer_registry_lock_malformed", path=str(lock_path))
            continue
        if not _pid_is_alive(record.pid):
            logger.debug("writer_registry_lock_stale_ignored", path=str(lock_path), pid=record.pid)
            continue
        verdict = lock_identity(record)
        if verdict == "ghost":
            logger.warning(
                "writer_registry_pid_reuse_ghost",
                pid=record.pid,
                path=str(lock_path),
                registered_epoch=record.registered_epoch,
            )
            continue
        if verdict == "unverified":
            identity_state = "unverified"
        pids.add(record.pid)
    return sorted(pids), "measured", identity_state


def _measure_writers(
    trw_dir: Path, *, pin_ttl_hours: int | None
) -> tuple[list[int], CensusState, IdentityState, HeartbeatState]:
    """Scan the registry and apply the pin-heartbeat TTL filter (PRD-CORE-248-FR05)."""

    pids, census_state, identity_state = _scan_writer_locks(trw_dir)
    if census_state == "unreadable":
        return pids, census_state, identity_state, "unavailable"
    if not pids:
        # No writers to filter: the heartbeat question is answered, vacuously.
        return pids, census_state, identity_state, "measured"
    if pin_ttl_hours is None:
        # The filter was not asked to run, so no heartbeat was consulted.
        return pids, census_state, identity_state, "unavailable"

    stale, heartbeat_state = stale_heartbeat_pids(
        trw_dir,
        pin_ttl_hours=pin_ttl_hours,
        counted_pids=set(pids),
    )
    for pid in sorted(stale):
        logger.debug("writer_heartbeat_stale_ignored", pid=pid, pin_ttl_hours=pin_ttl_hours)
    return [pid for pid in pids if pid not in stale], census_state, identity_state, heartbeat_state


def live_memory_writer_pids(trw_dir: Path, *, pin_ttl_hours: int | None = None) -> list[int]:
    """Return sorted live writer PIDs from ``memory.db.writers/*.lock``.

    Kept as a narrow list-returning surface for the two consumers that need only
    the PIDs and no pressure verdict: the WAL trigger evaluation
    (``state/_wal_triggers.py``) and the memory doctor
    (``server/_doctor_memory_wal.py``).
    """

    return _measure_writers(trw_dir, pin_ttl_hours=pin_ttl_hours)[0]


def take_writer_census(
    trw_dir: Path,
    *,
    threshold: int,
    pin_ttl_hours: int | None = None,
) -> WriterCensus:
    """Measure the writer registry once and decide whether it is under pressure.

    ``under_pressure`` is exactly ``peer_writer_count >= threshold``. Peer
    writers exclude the calling process: self-only registration is the normal
    steady state for a stdio per-instance MCP server and must never defer, which
    is what turned the deferral path into a permanent skip before PRD-FIX-080's
    follow-up. There is no internal clamp on *threshold*; the ``ge=2`` Field
    floor on ``session_start_writer_pressure_threshold`` is the only one, and a
    threshold of 1 peer would restore precisely the behaviour FR01 deletes.

    Callers must still honour ``session_start_defer_under_writer_pressure``:
    this function reports the measurement, not the operator's policy.
    """

    pids, census_state, identity_state, heartbeat_state = _measure_writers(trw_dir, pin_ttl_hours=pin_ttl_hours)
    self_pid = os.getpid()
    peer_count = sum(1 for pid in pids if pid != self_pid)
    return WriterCensus(
        writer_pids=tuple(pids),
        writer_count=len(pids),
        peer_writer_count=peer_count,
        threshold=threshold,
        under_pressure=peer_count >= threshold,
        census_state=census_state,
        identity_state=identity_state,
        heartbeat_state=heartbeat_state,
    )


def writer_pressure_details(census: WriterCensus, decision: DeferralDecision) -> dict[str, object]:
    """Build the compact writer-pressure deferral advisory block.

    THE single builder for the ``*_deferred`` advisory shape emitted across the
    session-start / ceremony / embeddings-maintenance paths; a literal advisory
    dict anywhere else in the tree is a defect. Before PRD-CORE-257-FR04 the
    block was ``{reason, writer_count, threshold}`` plus an optional legacy
    reason, so a reader could not tell a first deferral from the thousandth —
    which is what let a permanent skip pass for an ordinary one. It now carries
    the streak's age and count, and the two measurement states behind them.

    The reason vocabulary is single-valued under FR01, so the legacy
    ``defer_reason`` key and the switch that used to retain it are gone.
    ``writer_pids`` stay in the structlog events only — a full pid list in every
    deferral block is diagnostic noise for the calling LLM.
    """
    return {
        "reason": "writer_pressure",
        "writer_count": census.writer_count,
        "peer_writer_count": census.peer_writer_count,
        "threshold": census.threshold,
        "deferral_age_hours": round(decision.age_hours, 2),
        "deferred_count": decision.deferred_count,
        "census_state": census.census_state,
        "ledger_state": decision.ledger_state,
    }


__all__ = [
    "CensusState",
    "WriterCensus",
    "live_memory_writer_pids",
    "take_writer_census",
    "writer_pressure_details",
]
