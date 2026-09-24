"""Process birth identity: a pid plus its start time names exactly one process.

A pid alone is recycled by the OS once its process exits, so a record that must
name ONE process (the client that launched an MCP server, PRD-INFRA-189 FR08)
stores both. Kept a leaf so ``_pin_store``, ``_paths_pin_mgmt``, ``_pin_ttl``,
the learn-journal claims and the doctor share one answer to "is *pid* alive, and
when did it start".
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

#: Clock-granularity slack, not a policy threshold: a start time and a
#: ``time.time()`` stamp come from different clocks, so a process that records
#: itself the instant it starts can read back a start a fraction of a second
#: later. One second is far below any realistic pid-reuse interval.
BIRTH_EPOCH_SLACK_SECONDS = 1.0

# sysctl({CTL_KERN, KERN_PROC, KERN_PROC_PID, pid}) fills one ``struct kinfo_proc``
# (648 bytes on 64-bit macOS). Its first member, ``kp_proc.p_starttime``, is a
# ``struct timeval``: int64 tv_sec then int32 tv_usec. An absent pid yields size 0.
_KERN_PROC_PID_MIB = (1, 14, 1)
_KINFO_PROC_SIZE = 648


@lru_cache(maxsize=4)
def process_start_time(pid: int) -> str | None:
    """Cached :func:`read_process_start_time`, for THIS process's own identity checks.

    The cache is safe only for a process that outlives the caller's interest in it
    (this server's own client). Anything that watches ANOTHER process across time
    must call :func:`read_process_start_time`, or it keeps seeing a process that has
    exited (a long-running ``formation watch`` did, until C review SF1).
    """
    return read_process_start_time(pid)


def read_process_start_time(pid: int) -> str | None:
    """Return a numeric birth-time token for *pid*, or None when it cannot be read. Uncached.

    Paired with a pid it identifies one process: a pid alone is recycled by the
    OS after the process exits (PRD-INFRA-189 FR08). Linux reads the start tick
    from ``/proc``; macOS reads the kernel's start timeval in microseconds, since
    ``ps -o lstart=`` is whole seconds and a pid recycled within one second would
    match (Codex review 3, F7). Other platforms return None -- no adoption.
    Neither source depends on the reader's locale or timezone.
    """
    try:
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            # Field 22 (starttime); comm (field 2) may contain spaces, so split after it.
            return stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]
        if sys.platform != "darwin":
            return None
        mib = (ctypes.c_int * 4)(*_KERN_PROC_PID_MIB, pid)
        buf = ctypes.create_string_buffer(_KINFO_PROC_SIZE)
        size = ctypes.c_size_t(_KINFO_PROC_SIZE)
        if ctypes.CDLL(None).sysctl(mib, 4, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        if size.value != _KINFO_PROC_SIZE:
            return None
        seconds, micros = struct.unpack_from("=qi", buf, 0)
    except (OSError, IndexError, ValueError, AttributeError, struct.error):  # trw-fail-silent-allow: None never matches
        return None
    return f"{seconds}{micros:06d}"


def process_start_epoch(pid: int) -> float | None:
    """Wall-clock epoch at which *pid* started, or None when it cannot be observed.

    Built on :func:`read_process_start_time`: macOS's token is already epoch
    microseconds; Linux's is clock ticks since boot, added to ``/proc/stat``'s
    ``btime``. (Never the ``/proc/<pid>`` directory's ``st_ctime``: that tracks
    when the procfs entry was instantiated, measured 73 minutes late.)
    """
    token = read_process_start_time(pid)
    if token is None:
        return None
    if sys.platform == "darwin":
        return int(token) / 1_000_000
    hz = os.sysconf("SC_CLK_TCK")
    boot = _boot_epoch()
    return None if boot is None or hz <= 0 else boot + float(token) / hz


def _boot_epoch() -> float | None:
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):  # trw-fail-silent-allow: no btime means no observable start time
        return None
    return None


def pid_is_alive(pid: int) -> bool:
    """Whether *pid* appears to name a live local process (``signal 0``; EPERM counts as alive)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError:  # trw-fail-silent-allow: unknowable without psutil, so the pid is kept, never dropped
            return True
        return bool(psutil.pid_exists(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # trw-fail-silent-allow: ProcessLookupError IS "no such process"
        return False
    except OSError:  # trw-fail-silent-allow: EPERM and friends mean the pid exists
        return True
    return True


def parse_heartbeat_ts(value: object) -> datetime | None:
    """A pins.json ``last_heartbeat_ts`` (``..%fZ`` or plain ISO 8601) as aware UTC, or None when malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().removesuffix("Z"))
    except ValueError:  # trw-fail-silent-allow: a malformed heartbeat reads as absent; callers treat that as unknown
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
