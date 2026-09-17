"""Fail-closed process-group identity checks for persisted dispatch jobs.

Linux reads procfs. Darwin reads ``ps`` + ``sysctl kern.boottime`` (the same
source ``trw_swarm.runtime.path_lease`` uses); its start time has one-second
granularity, so a PID reused within the same second is undetectable there.
Every other platform fails closed: no identity, no group signal.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_BOOTTIME_SEC_RE = re.compile(r"sec\s*=\s*(\d+)")
_PROBE_TIMEOUT_S = 2.0
# ``ps -o lstart=`` renders in the caller's TZ/locale; the sidecar writer (runner
# env without TZ) and the MCP server must render the SAME string or every cancel
# is refused as an identity mismatch. Pin the probe environment, as
# trw_swarm.runtime.path_lease does.
_PROBE_ENV = {"LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
_boot_id_cache: str | None = None


def _probe_stdout(argv: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, check=False, env=_PROBE_ENV
    )
    return completed.stdout if completed.returncode == 0 else ""


def _darwin_boot_id() -> str | None:
    """``kern.boottime`` seconds, memoized: constant for the life of this process."""
    global _boot_id_cache
    if _boot_id_cache is None:
        match = _BOOTTIME_SEC_RE.search(_probe_stdout(["sysctl", "-n", "kern.boottime"]))
        _boot_id_cache = match.group(1) if match else None
    return _boot_id_cache


def _capture_identity_darwin(pid: int) -> dict[str, str | int] | None:
    """Darwin: the child must lead its own process group and not be a zombie."""
    try:
        line = _probe_stdout(["ps", "-o", "pid=,pgid=,stat=,lstart=", "-p", str(pid)]).strip()
        parts = line.split(None, 3)
        if len(parts) != 4 or int(parts[0]) != pid or int(parts[1]) != pid or parts[2].startswith("Z"):
            return None
        boot_id = _darwin_boot_id()
        starttime = parts[3].strip()
        if boot_id is None or not starttime:
            return None
        return {"pid": pid, "starttime": starttime, "boot_id": boot_id}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        logger.warning("dispatch_identity_capture_failed", pid=pid, platform="darwin", reason=type(exc).__name__)
        return None


def capture_identity(pid: int) -> dict[str, str | int] | None:
    """Capture a session/group leader identity; unsupported platforms fail closed."""
    try:
        if type(pid) is not int or pid <= 1 or pid in (os.getpid(), os.getpgrp()):
            return None
        if sys.platform == "darwin":
            return _capture_identity_darwin(pid)
        # comm can contain spaces and parentheses; fields after its LAST ')' begin at state.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if int(fields[2]) != pid or int(fields[3]) != pid or fields[0] == "Z":
            return None
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not boot_id or not fields[19].isdigit():
            return None
        return {
            "pid": pid,
            "starttime": fields[19],
            "boot_id": boot_id,
        }
    except (OSError, ValueError, IndexError, AttributeError) as exc:
        logger.warning("dispatch_identity_capture_failed", pid=pid, platform=sys.platform, reason=type(exc).__name__)
        return None


def signal_group(identity: dict[str, str | int] | None, sig: int) -> bool:
    """Revalidate before signaling; legacy/missing identities fail closed.

    starttime and boot ID prevent stale persisted PIDs from matching ordinary
    reuse. Check and killpg are not atomic: an external concurrent reaper can
    still race them. This is not a pidfd-backed process-group capability.
    """
    try:
        pid = identity["pid"] if identity else None
        if type(pid) is not int or capture_identity(pid) != identity:
            logger.warning("dispatch_signal_refused", pid=pid, reason="unverified_process_identity")
            return False
        os.killpg(pid, sig)
        return True
    except (OSError, KeyError, TypeError):
        logger.warning("dispatch_signal_refused", reason="identity_or_signal_error")
        return False
