"""Stop the memory daemons a test left running (PRD-CORE-280 test infrastructure).

A client that finds no daemon starts one (``start_daemon_detached``), detached,
with a 30-minute idle exit. Every test runs under its own HOME and
``TRW_USER_DIR``, so every test that reaches the store without the
``daemon_checkout`` fixture — in process, or in an ``update-project`` or CLI
subprocess — got a daemon of its own that outlived the run: one ``-n 8`` run
left ~450 of them.

A daemon is identified by the discovery file it publishes in its user memory
directory, which a test always places under its own tmp tree, and at session end
also by a working directory under the basetemp. Only a process whose command
line is the daemon's is signalled, so a recycled pid is never hit.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

_DISCOVERY_FILE = "daemon.json"
_DAEMON_ARGV_MARK = "trw_memory.server serve"
_GRACE_SECONDS = 5.0


def _is_daemon(pid: int) -> bool:
    try:
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout
    except (
        OSError
    ):  # trw-fail-silent-allow: ps unavailable: the pid is not known to be a daemon, so it is never signalled
        return False
    return _DAEMON_ARGV_MARK in command


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # trw-fail-silent-allow: no such process means not alive
        return False
    except PermissionError:
        return True
    return True


def daemon_pids_under(root: Path) -> list[int]:
    """Pids of live daemons whose discovery file lies under *root*."""
    pids: list[int] = []
    if not root.is_dir():
        return pids
    for discovery in root.rglob(_DISCOVERY_FILE):
        try:
            pid = int(json.loads(discovery.read_text(encoding="utf-8"))["pid"])
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ):  # trw-fail-silent-allow: an unreadable or partial discovery file names no daemon to stop
            continue
        if pid > 0 and pid != os.getpid() and _is_daemon(pid):
            pids.append(pid)
    return pids


def daemon_pids_in_cwd(root: Path) -> list[int]:
    """Pids of live daemons whose working directory lies under *root*.

    Catches a daemon whose discovery file is already gone (a fixture removed its
    tmp tree first): an auto-started daemon inherits the cwd of the process that
    started it, which for a subprocess test is the project under the basetemp.
    """
    listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False).stdout
    wanted = str(root.resolve())
    pids: list[int] = []
    for line in listing.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if _DAEMON_ARGV_MARK not in command or not pid_text.isdigit():
            continue
        cwd = _cwd(int(pid_text))
        if cwd is not None and (cwd == wanted or cwd.startswith(wanted + os.sep)):
            pids.append(int(pid_text))
    return pids


def _cwd(pid: int) -> str | None:
    try:
        return str(Path(os.readlink(f"/proc/{pid}/cwd")).resolve())
    except OSError:  # trw-fail-silent-allow: no /proc (macOS): fall through to lsof
        pass
    out = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True, check=False
    ).stdout
    names = [line[1:] for line in out.splitlines() if line.startswith("n")]
    return str(Path(names[0]).resolve()) if names else None


def reap_daemons_under(*roots: Path, wait: bool = False, by_cwd: bool = False) -> list[int]:
    """Stop every daemon published (or, with *by_cwd*, running) under any of *roots*.

    SIGTERM lets the daemon remove its discovery file and lock. With *wait*, a
    daemon still alive after a grace period gets SIGKILL; a per-test reap does not
    wait, and the session-end sweep does.
    """
    pids = {pid for root in roots for pid in daemon_pids_under(root)}
    if by_cwd:
        pids |= {pid for root in roots for pid in daemon_pids_in_cwd(root)}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:  # trw-fail-silent-allow: the process already exited, which is the goal
            continue
    if wait:
        deadline = time.monotonic() + _GRACE_SECONDS
        while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
            time.sleep(0.05)
        for pid in pids:
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:  # trw-fail-silent-allow: the process already exited, which is the goal
                    pass
    return sorted(pids)
