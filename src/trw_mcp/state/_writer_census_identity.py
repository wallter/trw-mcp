"""Writer-lock identity and heartbeat validation for the writer census.

PRD-CORE-257-FR10. Split out of ``memory_pressure.py`` so that module stays a
thin census + advisory surface: this one owns the two questions that decide
whether a registered PID is *evidence of a live peer writer*.

1. **Identity.** ``trw-memory`` writes two lines into every writer lock — the
   PID and the wall-clock epoch at which it registered
   (``trw-memory/src/trw_memory/storage/_writer_registry.py``). The previous
   reader parsed only the first line, so a PID recycled by the kernel
   resurrected a dead writer and inflated the census. Where the operating
   system exposes process birth time (the ``/proc`` entry on Linux) a process
   whose birth POSTDATES its lock's registration epoch cannot be the process
   that wrote the lock, and is excluded. Where birth time is unavailable the
   PID stays counted and the census reports ``identity_state="unverified"``
   rather than implying it was checked.

2. **Heartbeat.** ``.trw/runtime/pins.json`` carries a per-session heartbeat.
   The previous reader accepted a ``trw_dir`` and then called the process-global
   ``load_pin_store()``, which resolves its own path, so under a non-default
   project it filtered against another project's pins. The read here is scoped
   to the ``trw_dir`` it is given.

**This module never unlinks a lock file.** Pruning is owned by
``WriterRegistry._prune_stale_peers`` in ``trw-memory``, which runs in the same
process that creates locks with ``O_EXCL``; a second pruner in another package
would race that exclusive create. Dead locks are logged at DEBUG and dropped
from the count, nothing more.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

IdentityState = Literal["verified", "unverified"]
"""``verified`` when every counted lock's identity was checkable; else ``unverified``."""

HeartbeatState = Literal["measured", "partial", "unavailable"]
"""``measured`` = every counted pid had a usable heartbeat; ``partial`` = some did
not (no entry, or an implausible one); ``unavailable`` = no pin store, a load
failure, or the filter was not asked to run."""

LockVerdict = Literal["verified", "ghost", "unverified"]

# Clock-granularity slack, NOT a policy threshold (so NFR05's "no magic numbers"
# rule does not apply: it is not standing in for a tunable). ``/proc`` inode
# timestamps and ``time.time()`` are both wall clock but are sampled by
# different subsystems, so a process that registers its lock in the same instant
# it starts can read back a birth time a fraction of a second after the
# registration epoch. One second is far below any realistic PID-reuse interval,
# so the slack cannot mask a ghost.
_BIRTH_EPOCH_SLACK_SECONDS = 1.0

# A heartbeat this far ahead of our clock cannot have been written by a clock
# that agrees with ours. FR10 requires it to be treated as UNMEASURED rather
# than as fresh — a future timestamp would otherwise pass every staleness test
# forever.
_FUTURE_HEARTBEAT_SKEW_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class LockRecord:
    """One parsed writer lock file: its PID and its registration epoch."""

    pid: int
    registered_epoch: float | None


def read_writer_lock(lock_path: Path) -> LockRecord | None:
    """Parse a writer lock file into a :class:`LockRecord`, or ``None``.

    Returns ``None`` for malformed CONTENT: an empty file or a non-numeric
    first line. Raises ``OSError``/``UnicodeDecodeError`` when the file
    itself could not be read (permission denied, race-deleted mid-scan,
    undecodable bytes) — the caller must treat that as a census-degrading
    event, not as "no writer here" (PRD-CORE-257 audit row 4): an unreadable
    lock is not evidence of absence, it is a measurement failure. A missing
    or non-numeric SECOND line yields ``registered_epoch=None`` — the lock is
    still a real writer, its identity simply cannot be validated.
    """
    lines = lock_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    try:
        pid = int(lines[0].strip())
    except (
        ValueError
    ):  # trw-fail-silent-allow: a non-numeric first line is a malformed lock, logged by the caller at DEBUG
        return None
    epoch: float | None = None
    if len(lines) > 1:
        try:
            parsed = float(lines[1].strip())
        except ValueError:
            parsed = 0.0
        if parsed > 0.0:
            epoch = parsed
    return LockRecord(pid=pid, registered_epoch=epoch)


def _boot_epoch() -> float | None:
    """Wall-clock epoch of the last boot, from ``/proc/stat``'s ``btime`` line."""
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (
        OSError,
        ValueError,
        IndexError,
    ):  # trw-fail-silent-allow: without btime no birth time is observable, which the caller reports as unverified
        return None
    return None


def process_birth_epoch(pid: int) -> float | None:
    """Return the wall-clock epoch at which *pid* started, when observable.

    Linux exposes it as field 22 of ``/proc/<pid>/stat`` — the process start
    time in clock ticks since boot — which is added to ``/proc/stat``'s
    ``btime``. Every other platform returns ``None``, which the caller reports
    as ``identity_state="unverified"``, never as a false exclusion.

    **Do not substitute the ``/proc/<pid>`` directory's ``st_ctime``.** It reads
    like a creation time and is not one: measured on this box, a server started
    at epoch 1788557135 reported ``st_ctime`` 1788561547 — 73 minutes late,
    because the procfs inode timestamp tracks when the entry was last
    instantiated, not when the task began. Using it classified all FIVE live
    writers in the repo registry as PID-reuse ghosts and drove the census to
    zero, i.e. pressure could never fire.
    """
    if sys.platform != "linux":
        return None
    boot = _boot_epoch()
    if boot is None:
        return None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            raw = handle.read()
        # The comm field is parenthesised and may itself contain spaces, so the
        # numeric fields are counted from the LAST close-paren, not from split().
        fields = raw[raw.rindex(")") + 2 :].split()
        ticks = float(fields[19])
    except (
        OSError,
        ValueError,
        IndexError,
    ):  # trw-fail-silent-allow: an unobservable birth time is reported as identity_state="unverified", never as a false exclusion
        return None
    hz = os.sysconf("SC_CLK_TCK")
    if hz <= 0:
        return None
    return boot + ticks / hz


def lock_identity(record: LockRecord) -> LockVerdict:
    """Classify a lock as ``verified``, a PID-reuse ``ghost``, or ``unverified``."""
    if record.registered_epoch is None:
        return "unverified"
    birth = process_birth_epoch(record.pid)
    if birth is None:
        return "unverified"
    if birth > record.registered_epoch + _BIRTH_EPOCH_SLACK_SECONDS:
        return "ghost"
    return "verified"


def scoped_pin_store_path(trw_dir: Path) -> Path:
    """Return the pin-store path for *this* project.

    Mirrors ``trw_mcp.state._pin_store.pin_store_path()``, which takes no
    ``trw_dir`` and resolves the process-global one. ``_pin_store.py`` sits at
    exactly the 350 effective-LOC gate, so the scoped read lives here instead;
    a contract test asserts the two definitions resolve to the same path under
    the default ``trw_dir`` so they cannot drift apart (plan decision OD-2).
    """
    return trw_dir / "runtime" / "pins.json"


def _parse_heartbeat_ts(value: object) -> datetime | None:
    """Parse a pins.json ``last_heartbeat_ts`` into an aware UTC datetime.

    Accepts the ``_iso_now`` format (``...%f`` + trailing ``Z``) as well as
    plain ISO8601. Returns ``None`` on any malformed value so the heartbeat
    filter fails open — the PID stays counted rather than being dropped on a
    parse error.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().removesuffix("Z"))
    except (
        ValueError
    ):  # trw-fail-silent-allow: a malformed heartbeat keeps the pid COUNTED (fail-open) and degrades heartbeat_state
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _freshest_heartbeats(trw_dir: Path) -> dict[int, datetime] | None:
    """Build ``pid -> freshest heartbeat`` from the scoped pin store.

    ``None`` means the store could not be read at all (no file, bad JSON, wrong
    shape) — reported as ``heartbeat_state="unavailable"``, never as "no stale
    pids found". A heartbeat further ahead of our clock than
    ``_FUTURE_HEARTBEAT_SKEW_SECONDS`` is implausible skew and is dropped, so
    the PID reads as having no usable heartbeat instead of as permanently fresh.
    """
    import json

    path = scoped_pin_store_path(trw_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("writer_heartbeat_pins_load_failed", path=str(path), exc_info=True)
        # trw-fail-silent-allow: None is the distinct "unavailable" state the caller reports, not an empty result
        return None
    if not isinstance(raw, dict):
        return None

    horizon = datetime.now(timezone.utc).timestamp() + _FUTURE_HEARTBEAT_SKEW_SECONDS
    freshest: dict[int, datetime] = {}
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        pid_raw = entry.get("pid")
        if not isinstance(pid_raw, int):
            continue
        heartbeat = _parse_heartbeat_ts(entry.get("last_heartbeat_ts"))
        if heartbeat is None:
            continue
        if heartbeat.timestamp() > horizon:
            logger.warning("writer_heartbeat_implausible_future", pid=pid_raw, ts=str(heartbeat))
            continue
        current = freshest.get(pid_raw)
        if current is None or heartbeat > current:
            freshest[pid_raw] = heartbeat
    return freshest


def stale_heartbeat_pids(
    trw_dir: Path,
    *,
    pin_ttl_hours: int,
    counted_pids: set[int],
) -> tuple[set[int], HeartbeatState]:
    """Return the counted PIDs whose freshest heartbeat is past the TTL.

    Fail-open in both directions: a PID with no usable heartbeat entry is NOT
    dropped (it may be a non-ceremony writer), and an unreadable pin store drops
    nobody. The second element of the pair says which of those two facts held,
    so a caller can tell "every counted writer was heartbeat-checked" from
    "some of them were not".
    """
    if pin_ttl_hours <= 0 or not counted_pids:
        return set(), "unavailable"
    freshest = _freshest_heartbeats(trw_dir)
    if freshest is None:
        return set(), "unavailable"

    cutoff = datetime.now(timezone.utc).timestamp() - pin_ttl_hours * 3600
    stale = {pid for pid in counted_pids if pid in freshest and freshest[pid].timestamp() < cutoff}
    covered = counted_pids <= set(freshest)
    return stale, "measured" if covered else "partial"


__all__ = [
    "HeartbeatState",
    "IdentityState",
    "LockRecord",
    "LockVerdict",
    "lock_identity",
    "process_birth_epoch",
    "read_writer_lock",
    "scoped_pin_store_path",
    "stale_heartbeat_pids",
]
