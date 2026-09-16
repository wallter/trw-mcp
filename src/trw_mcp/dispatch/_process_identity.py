"""Fail-closed Linux process-group identity checks for persisted dispatch jobs."""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def capture_identity(pid: int) -> dict[str, str | int] | None:
    """Capture a session leader identity; non-Linux POSIX deliberately fails closed."""
    try:
        if type(pid) is not int or pid <= 1 or pid in (os.getpid(), os.getpgrp()):
            return None
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
    except (OSError, ValueError, IndexError, AttributeError):
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
