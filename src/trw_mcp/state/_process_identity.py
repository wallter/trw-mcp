"""Process birth identity: a pid plus its start time names exactly one process.

A pid alone is recycled by the OS once its process exits, so a record that must
name ONE process (the client that launched an MCP server, PRD-INFRA-189 FR08)
stores both. Kept a leaf so ``_pin_store`` and ``_paths_pin_mgmt`` share it.
"""

from __future__ import annotations

import ctypes
import struct
import sys
from functools import lru_cache
from pathlib import Path

# sysctl({CTL_KERN, KERN_PROC, KERN_PROC_PID, pid}) fills one ``struct kinfo_proc``
# (648 bytes on 64-bit macOS). Its first member, ``kp_proc.p_starttime``, is a
# ``struct timeval``: int64 tv_sec then int32 tv_usec. An absent pid yields size 0.
_KERN_PROC_PID_MIB = (1, 14, 1)
_KINFO_PROC_SIZE = 648


@lru_cache(maxsize=4)
def process_start_time(pid: int) -> str | None:
    """Return a numeric birth-time token for *pid*, or None when it cannot be read.

    Paired with a pid it identifies one process: a pid alone is recycled by the
    OS after the process exits (PRD-INFRA-189 FR08). Linux reads the start tick
    from ``/proc``; macOS reads the kernel's start timeval in microseconds, since
    ``ps -o lstart=`` is whole seconds and a pid recycled within one second would
    match (Codex review 3, F7). Other platforms return None -- no adoption.
    Neither source depends on the reader's locale or timezone. Cached per pid.
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
