"""The trw-mcp stdio servers still running against a checkout, and the clients that launched them.

A server records itself in the checkout's ``.trw/runtime/pins.json`` when it pins
a run: its pid, its launching client's pid, and that client's birth time
(PRD-INFRA-189 FR08). A server started before an upgrade runs the old code until
its client reconnects, and an old server keeps writing the checkout store that
``memory migrate`` just emptied, so the migration names each one. A server that
never pinned a run is not listed by those two; :func:`running_servers` also finds the
unpinned ones from the process table (a server whose working directory is the checkout).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from trw_memory.daemon._discovery import AGENT_MUST_NOT_STOP

from trw_mcp.state._process_identity import (
    BIRTH_EPOCH_SLACK_SECONDS,
    parse_heartbeat_ts,
    pid_is_alive,
    process_start_epoch,
    read_process_start_time,
)

__all__ = ["live_servers", "running_servers", "stray_servers"]


def _process_name(pid: int) -> str:
    argv = ["ps", "-o", "comm=", "-p", str(pid)]
    try:
        name = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False).stdout.strip()  # noqa: S603 -- fixed argv, no shell
    except (OSError, subprocess.TimeoutExpired):  # trw-fail-silent-allow: no ps (Windows) names the client by pid only
        return "a client"
    return Path(name).name or "a client"


def _live(trw_dir: Path) -> list[tuple[int, int | None, float]]:
    """``(server pid, live client pid or None, recorded epoch)`` per live server in pins.json."""
    try:
        pins = json.loads((trw_dir / "runtime" / "pins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: no readable pins file records no server
        return []
    found: list[tuple[int, int | None, float]] = []
    for record in pins.values() if isinstance(pins, dict) else ():
        pid = record.get("pid") if isinstance(record, dict) else None
        if not isinstance(pid, int) or pid == os.getpid() or not pid_is_alive(pid):
            continue
        started, created = process_start_epoch(pid), parse_heartbeat_ts(record.get("created_ts"))
        if started is None or created is None or started > created.timestamp() + BIRTH_EPOCH_SLACK_SECONDS:
            continue  # the pid was reused after this record was written
        client = record.get("client_pid")
        alive = isinstance(client, int) and read_process_start_time(client) == record.get("client_start")
        found.append((pid, client if alive else None, created.timestamp()))
    return found


def _orphan(pid: int) -> str:
    return f"trw-mcp pid {pid} is orphaned (its client exited); the user can stop process {pid}. {AGENT_MUST_NOT_STOP}"


def live_servers(trw_dir: Path) -> list[str]:
    """One line per live server recorded in *trw_dir*'s pins.json, naming the client to reconnect."""
    return [
        _orphan(pid)
        if client is None
        else f"trw-mcp pid {pid}, launched by {_process_name(client)} (pid {client}): reconnect it"
        for pid, client, _ in _live(trw_dir)
    ]


def stray_servers(trw_dir: Path) -> list[str]:
    """Servers no client will use again: orphans, and all but the newest server under one client."""
    servers = {pid: (client, created) for pid, client, created in _live(trw_dir)}  # a pid pinning two runs counts once
    newest: dict[int, tuple[float, int]] = {}
    for pid, (client, created) in servers.items():
        if client is not None:
            newest[client] = max((created, pid), newest.get(client, (created, pid)))
    return [
        _orphan(pid)
        if client is None
        else (
            f"trw-mcp pid {pid} is superseded by a newer server under client pid {client}; the user can stop "
            f"process {pid}. {AGENT_MUST_NOT_STOP}"
        )
        for pid, (client, _) in servers.items()
        if client is None or newest[client][1] != pid
    ]


_SERVER_EXECUTABLES = frozenset({"trw-mcp", "trw-mcp-proxy"})


def _is_server_command(command: str) -> bool:
    """A ``trw-mcp`` stdio server or proxy command line, never a one-shot verb such as ``trw-mcp doctor``."""
    tokens = command.split()
    for i, word in enumerate(tokens):
        module = word == "trw_mcp.server" and i > 0 and tokens[i - 1] == "-m"
        if module or Path(word).name in _SERVER_EXECUTABLES:
            rest = tokens[i + 1 :]
            return not rest or rest[0] == "serve" or rest[0].startswith("-")
    return False


def _run(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603 -- fixed argv, no shell
    except (OSError, subprocess.TimeoutExpired):  # trw-fail-silent-allow: None is reported as "could not check"
        return None
    return done.stdout if done.returncode == 0 or done.stdout else None


def _cwds(pids: list[int]) -> dict[int, str] | None:
    """Working directory per pid: ``/proc`` on Linux, one ``lsof`` call elsewhere; None when neither can say."""
    if Path("/proc/self/cwd").exists():
        found: dict[int, str] = {}
        for pid in pids:
            try:
                found[pid] = os.readlink(f"/proc/{pid}/cwd")
            except OSError:  # trw-fail-silent-allow: an exited or another user's process has no readable cwd
                continue
        return found
    out = _run(["lsof", "-a", "-d", "cwd", "-p", ",".join(map(str, pids)), "-Fpn"])
    if out is None:
        return None
    found = {}
    current: int | None = None
    for line in out.splitlines():
        if line[:1] == "p" and line[1:].isdigit():
            current = int(line[1:])
        elif line[:1] == "n" and current is not None:
            found[current] = line[1:]
    return found


def running_servers(project: Path) -> set[int] | None:
    """Pids of the live trw-mcp servers on *project*: recorded in its pins.json, or running with it as their cwd.

    None when the process table cannot be read (Windows, no ``ps``/``lsof``), so a caller says "could not check"
    instead of "none".
    """
    pinned = {pid for pid, _client, _created in _live(project / ".trw")}
    table = _run(["ps", "-axo", "pid=,command="]) if os.name == "posix" else None
    if table is None:
        return None
    candidates = []
    for row in table.splitlines():
        pid_text, _, command = row.strip().partition(" ")
        if pid_text.isdigit() and int(pid_text) != os.getpid() and _is_server_command(command):
            candidates.append(int(pid_text))
    cwds = _cwds(candidates) if candidates else {}
    if cwds is None:
        return None
    root = os.path.realpath(project)
    return pinned | {pid for pid, cwd in cwds.items() if os.path.realpath(cwd) == root}
