"""Starting the memory daemon through the shared env's recorded interpreter (DAEMON-AUTOSTART-VERSION-RACE).

After a daemon drain, the FIRST memory client to call starts the replacement, and left alone it starts it from its
OWN interpreter. 2026-09-30: a repo-``.venv`` client (trw-memory 5.1.0.dev10) won that race once and published an
older daemon beside the 8.1.2 stable server. When ``shared_mcp`` is on and an env record (``envs.json``) names the
interpreter for the store the client is attaching to, the daemon starts from THAT interpreter, with that env's
``PYTHONPATH`` and ``TRW_USER_DIR`` and never the client's own; any other store (a scratch ``TRW_USER_DIR``, a project
with no record) keeps the client's own start.

It does not replace the hot-swap watcher (``_autoswap``): the watcher drains a strictly-older daemon of the running
server's own env; this closes the gap before it, so the daemon that replaces a drained one is the right one at once.

A record that names an interpreter which is gone, or an unreadable record, refuses the start (``DaemonUnreachableError``
naming the fix): falling back to the client's own interpreter is the bug being closed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trw_memory.daemon._paths import DaemonPaths
from trw_memory.daemon._spawn import SpawnedDaemon, start_daemon_detached
from trw_memory.exceptions import DaemonSecretUnreadableError, DaemonUnreachableError
from trw_memory.user_paths import user_memory_dir_path

if TYPE_CHECKING:
    from trw_memory.daemon.client import DaemonClient

    from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
    from trw_mcp.shared_server._records import SharedPaths

# Nothing from ``trw_mcp.models.config`` or ``_records`` is imported at module level: the hook's fast recall read
# builds its client through ``daemon_client``, and importing TRWConfig there costs ~110 ms (S3c).
Launcher = Callable[[DaemonPaths], SpawnedDaemon]
_CLIENT_ONLY = ("PYTHONPATH", "PYTHONHOME", "TRW_USER_DIR")  # an env's record decides these, never the client's shell
#: ``SharedPaths.root / "envs.json"`` relative to the trw dir (a test pins it to ``_records``): the cheap "is a
#: shared env configured for this project" probe that keeps the configless path configless.
_ENV_MAP = Path("runtime") / "shared-mcp" / "envs.json"


def shared_env_launcher(trw_dir: Path, config: SharedMcpConfig) -> Launcher | None:
    """The launcher for a client of the project at *trw_dir*, or ``None`` when ``shared_mcp`` is off (no change)."""
    from trw_mcp.shared_server._records import SharedPaths

    if not config.enabled:
        return None
    paths = SharedPaths.resolve(trw_dir, config)
    return lambda daemon_paths: _launch(paths, daemon_paths)


def daemon_client(token: str, trw_dir: Path, **kwargs: Any) -> DaemonClient:
    """A ``DaemonClient`` that autostarts through the shared env's interpreter when ``shared_mcp`` is on.

    With no ``envs.json`` for the project (the common case, and every hook read) nothing else is imported.
    """
    from trw_memory.daemon.client import DaemonClient

    if (trw_dir / _ENV_MAP).is_file():
        from trw_mcp.models.config import get_config

        launcher = shared_env_launcher(trw_dir, get_config().shared_mcp)
        if launcher is not None:
            kwargs["launcher"] = launcher
    return DaemonClient(token, **kwargs)


def _launch(paths: SharedPaths, daemon_paths: DaemonPaths) -> SpawnedDaemon:
    from trw_mcp.shared_server._records import STABLE, read_env_map

    try:
        mapping = read_env_map(paths)
    except (ValueError, TypeError, OSError, DaemonSecretUnreadableError) as exc:
        raise DaemonUnreachableError(
            f"{paths.root / 'envs.json'} cannot be read ({type(exc).__name__}), so no memory daemon was started: this "
            f"client's own interpreter might not be the one the shared env serves. Repair the file, or point the env "
            f"at an interpreter again with `trw-mcp swap --env {STABLE} --python <path>`."
        ) from exc
    env = _env_of(paths, mapping, daemon_paths.user_memory_dir)
    if env is None:
        return start_daemon_detached(daemon_paths)
    python = mapping[env]
    if not isinstance(python, str) or not Path(python).is_file():  # a malformed entry refuses like a gone interpreter
        raise DaemonUnreachableError(
            f"the shared env {env!r} records interpreter {python}, which does not exist, so no memory daemon was "
            f"started from this client's own interpreter. Point the env at a real one: "
            f"`trw-mcp swap --env {env} --version <V>` (or --python <path>)."
        )
    return start_daemon_detached(daemon_paths, python=python, environ=_environ(paths, env))


def _env_of(paths: SharedPaths, mapping: dict[str, str], memory_dir: Path) -> str | None:
    """The recorded env whose daemon store is *memory_dir*, else ``None`` (a store no env owns).

    Stable's store is the DEFAULT user dir (``TRW_USER_DIR`` masked: it names another store, never stable's).
    """
    default_dir = user_memory_dir_path({k: v for k, v in os.environ.items() if k != "TRW_USER_DIR"})
    for env in sorted(mapping):
        user_dir = paths.user_dir(env)
        if (default_dir if user_dir is None else (user_dir / "memory").resolve()) == memory_dir.resolve():
            return env
    return None


def _environ(paths: SharedPaths, env: str) -> dict[str, str]:
    """The client's environment, with the interpreter-selecting variables replaced by *env*'s own record."""
    from trw_mcp.shared_server._records import env_pythonpath

    child = {k: v for k, v in os.environ.items() if k not in _CLIENT_ONLY}
    if pythonpath := env_pythonpath(paths, env):  # a `swap --src` worktree: its source
        child["PYTHONPATH"] = pythonpath
    if (user_dir := paths.user_dir(env)) is not None:
        child["TRW_USER_DIR"] = str(user_dir)
    return child
