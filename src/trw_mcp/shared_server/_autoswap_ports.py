"""The ports a hot-swap poll touches: installed versions, the interpreter probe, locks, the persisted row.

Belongs to the ``_autoswap`` facade, which re-exports every public name here. Split out so the watcher's decisions
(``HotSwap``) read apart from the code that reaches the filesystem, subprocesses and the memory daemon.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata as metadata
import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trw_memory.daemon._discovery import DaemonInfo
from trw_memory.daemon._paths import read_secret_file, write_secret_file

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.shared_server._records import (
    SharedPaths,
    SharedServerError,
    env_python,
    env_pythonpath,
    read_live_record,
    validate_env,
)

Versions = dict[str, str]
#: An env's interpreter record: (python, PYTHONPATH of a ``swap --src`` worktree or ``None``).
Record = tuple[str, str | None]

_WATCHED = ("trw-mcp", "trw-memory", "trw-distill")
_PROBE_SECONDS = 60.0
_DRAIN_SECONDS = 5.0
_PROBE = """
import importlib.metadata as m, json
import trw_mcp
from trw_memory._version import __version__ as memory
out = {"trw-mcp": trw_mcp.__version__, "trw-memory": memory}
try:
    out["trw-distill"] = m.version("trw-distill")
except m.PackageNotFoundError:
    pass
print(json.dumps(out))
"""


# --------------------------------------------------------------------------- what is installed


def installed_versions() -> Versions:
    """The watched distributions' on-disk versions (the cheap hint); a missing one is omitted."""
    importlib.invalidate_caches()
    found: Versions = {}
    for name in _WATCHED:
        with contextlib.suppress(metadata.PackageNotFoundError, OSError, ValueError):
            if version := metadata.version(name):  # None: a dist-info whose METADATA is not written yet (mid-install)
                found[name] = version
    return found


def booted_versions() -> Versions:
    """The versions THIS process runs: what a probe of its own interpreter reports (captured once, at boot)."""
    from trw_memory._version import __version__ as memory

    import trw_mcp

    found: Versions = {"trw-mcp": trw_mcp.__version__, "trw-memory": memory}
    with contextlib.suppress(metadata.PackageNotFoundError, OSError, ValueError):
        if distill := metadata.version("trw-distill"):
            found["trw-distill"] = distill
    return found


def probe_interpreter(python: str, pythonpath: str | None) -> Versions | None:
    """What a server started on *python* (with *pythonpath* only when the env records one) would run, or ``None``.

    ``None`` is a failed import, a crash or unparseable output: a half-finished install. It is the same clean
    environment ``spawn_server`` gives the successor, so the probe answers for the process that would start.
    """
    from trw_mcp.shared_server._ops import _probe_env

    try:
        done = subprocess.run(  # noqa: S603 -- fixed argv; the interpreter is the env's own record
            [python, "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=_PROBE_SECONDS,
            check=False,
            env=_probe_env(pythonpath),
        )
    except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: an unrunnable interpreter IS the answer
        return None
    if done.returncode != 0:
        return None
    try:
        payload = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):  # trw-fail-silent-allow: unparseable probe output IS the answer (None = not ready)
        return None
    ok = isinstance(payload, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items())
    return dict(payload) if ok and payload.get("trw-mcp") and payload.get("trw-memory") else None


# --------------------------------------------------------------------------- locks and the persisted row


def claim_busy(paths: SharedPaths, env: str) -> bool:
    """Whether a successor holds the env's claim lock (it does from its start until its flip)."""
    try:
        handle = open(paths.lock(env), "a")  # noqa: SIM115 -- closed by the with below
    except OSError:  # trw-fail-silent-allow: no lock file or directory: no successor is booting
        return False
    with handle:
        try:
            _lock_ex_nb(handle.fileno())
        except OSError:
            return True
        _lock_un(handle.fileno())
    return False


@contextlib.contextmanager
def spawn_lock(paths: SharedPaths, env: str) -> Iterator[bool]:
    """The env's spawn lock, the one a proxy takes to start a server: ``True`` when held, never blocking."""
    paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(paths.root / f"{validate_env(env)}.spawn.lock", "a") as handle:
        try:
            _lock_ex_nb(handle.fileno())
            held = True
        except OSError:
            held = False
        try:
            yield held
        finally:
            if held:
                _lock_un(handle.fileno())


def _last_path(paths: SharedPaths, env: str) -> Path:
    # No ``.json``: ``SharedPaths.envs()`` reads every ``<name>.json`` beside the records as an env (cf. ``.serving-env``).
    return paths.root / f"{validate_env(env)}.last-auto-swap"


def write_last_auto_swap(paths: SharedPaths, env: str, row: dict[str, Any]) -> None:
    """Keep the last automatic swap (0600, atomic) for the successor, which answers ``trw_status``."""
    write_secret_file(_last_path(paths, env), json.dumps(row, sort_keys=True))


def read_last_auto_swap(paths: SharedPaths, env: str, *, live_pid: int | None) -> dict[str, Any] | None:
    """The last automatic swap, or ``None``. ``swapping`` whose old server is no longer the live one reads as ``swapped``."""
    try:
        raw = read_secret_file(_last_path(paths, env))
        row = json.loads(raw) if raw else None
    except (ValueError, OSError, RuntimeError):  # trw-fail-silent-allow: a status extra, never a failure
        return None
    if not isinstance(row, dict):
        return None
    if row.get("outcome") == "swapping" and live_pid is not None and live_pid != row.get("from_pid"):
        row["outcome"] = "swapped"
    return row


@dataclass(frozen=True)
class Ports:
    """Everything a poll touches outside its own state; tests inject fakes, ``real_ports`` binds the real ones."""

    installed: Callable[[], Versions]
    recorded: Callable[[], Record]
    probe: Callable[[str, str | None], Versions | None]
    live_pid: Callable[[], int | None]
    claim_busy: Callable[[], bool]
    spawn_lock: Callable[[], contextlib.AbstractContextManager[bool]]
    start_successor: Callable[[], Any]
    daemon: Callable[[], DaemonInfo | None]
    drain_daemon: Callable[[DaemonInfo, str], str]
    write_last: Callable[[dict[str, Any]], None]


# --------------------------------------------------------------------------- the real ports


def real_ports(env: str, paths: SharedPaths, project_root: Path, *, pid: int) -> Ports:
    """The ports bound to this env's records, its spawn machinery and its memory daemon."""

    def live_pid() -> int | None:
        try:
            record = read_live_record(paths, env)
        except SharedServerError:  # trw-fail-silent-allow: an untrusted record is not proof this server is the env's
            return None
        return None if record is None else record.pid

    def start_successor() -> Any:
        from trw_mcp.shared_server._ops import _SWAP_SECONDS
        from trw_mcp.shared_server._proxy import spawn_server, wait_published

        proc = spawn_server(paths, env, project_root=str(project_root), successor=True)
        return wait_published(paths, env, proc, seconds=_SWAP_SECONDS, not_pid=pid)

    def daemon_paths() -> Any:
        from trw_memory.daemon import DaemonPaths

        from trw_mcp.shared_server._ops import _memory_dir

        return DaemonPaths(user_memory_dir=_memory_dir(paths, env))

    def daemon() -> DaemonInfo | None:
        from trw_memory.daemon import read_live_discovery

        try:
            found = read_live_discovery(daemon_paths())
        except (OSError, RuntimeError, ValueError):  # trw-fail-silent-allow: no readable record: no daemon to replace
            return None
        return found if isinstance(found, DaemonInfo) else None

    def drain_daemon(observed: DaemonInfo, mine: str) -> str:
        from trw_memory.daemon import _upgrade
        from trw_memory.daemon._grants import read_checkout_grant
        from trw_memory.exceptions import DaemonAuthError

        current = daemon()  # the drain acts on whatever the record names NOW: only the instance that was judged older
        if current is None or (current.pid, current.started_at) != (observed.pid, observed.started_at):
            return "the daemon changed since it was judged older; nothing was drained"
        try:
            token = read_checkout_grant(project_root)
        except DaemonAuthError as exc:
            return f"no usable memory grant for the drain: {exc}"
        # A short window: the drain closes the daemon's door for other sessions, so a busy daemon is given up on fast.
        return _upgrade.drain_daemon(daemon_paths(), token=token, mine=mine, timeout=_DRAIN_SECONDS)

    return Ports(
        installed=installed_versions,
        recorded=lambda: (env_python(paths, env), env_pythonpath(paths, env)),
        probe=probe_interpreter,
        live_pid=live_pid,
        claim_busy=lambda: claim_busy(paths, env),
        spawn_lock=lambda: spawn_lock(paths, env),
        start_successor=start_successor,
        daemon=daemon,
        drain_daemon=drain_daemon,
        write_last=lambda row: write_last_auto_swap(paths, env, row),
    )
