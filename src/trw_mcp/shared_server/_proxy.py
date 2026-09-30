"""``trw-mcp-proxy``: the stdio shim a client launches in place of ``trw-mcp serve``.

A byte pass-through, not a second MCP server: each newline-delimited JSON-RPC
message from stdin is POSTed as-is to the env's shared server and the JSON
answer goes back on stdout. The client's own ``initialize`` reaches the server,
nothing is cached, and the proxy imports no FastMCP.

Identity: the proxy computes the pin key a stdio server would have resolved
from this same environment (``TRW_SESSION_ID``, then the client's session
variable, else a fresh per-process id) and sends it as ``Mcp-Session-Id``.

Failure is never silent. A request the server never applied (connection
refused, a drain or busy 503 from the door) is resent with bounded backoff
against a re-read discovery record, starting the server when absent. A request
sent and then lost is resent only for read-only methods; any other gets a
JSON-RPC error saying the outcome is unknown and naming the remedy.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from trw_memory.daemon._discovery import DaemonInfo

from trw_mcp.shared_server._records import (
    BUSY_HEADER,
    DRAINING_HEADER,
    SESSION_HEADER,
    SharedPaths,
    SharedServerError,
    env_python,
    env_pythonpath,
    env_serving_env,
    read_live_record,
    read_token,
    record_serving_env,
    serving_env_keys,
)

#: Methods with no side effect: safe to resend after a connection lost mid-request.
_READ_ONLY = frozenset(
    {"initialize", "ping", "tools/list", "resources/list", "resources/templates/list", "resources/read", "prompts/list"}
)
_UNKNOWN_OUTCOME = -32001
_UNAVAILABLE = -32002
_MAX_LINE = 64 * 1024 * 1024

Post = Callable[[str, bytes, dict[str, str]], Awaitable[httpx.Response]]


@dataclass(frozen=True)
class Target:
    url: str
    token: str


def client_identity() -> str:
    """The pin key a stdio server would resolve from this environment (layers 2, 2b, 4)."""
    from trw_mcp.client_profiles.session_identity import resolve_client_session_id

    return os.environ.get("TRW_SESSION_ID") or resolve_client_session_id() or f"proxy-{uuid.uuid4()}"


class Proxy:
    """Forwards one JSON-RPC line and returns the line to write back (``None``: nothing to write)."""

    def __init__(
        self,
        *,
        resolve: Callable[[], Awaitable[Target]],
        post: Post,
        identity: str,
        env: str,
        budget_seconds: float = 90.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._resolve, self._post, self._identity, self._env = resolve, post, identity, env
        self._budget, self._sleep = budget_seconds, sleep

    async def forward(self, line: bytes) -> bytes | None:
        try:
            message = json.loads(line)
        except ValueError:
            return _error(None, -32700, "parse error: the proxy received a line that is not JSON")
        rid = message.get("id") if isinstance(message, dict) else None
        method = message.get("method", "") if isinstance(message, dict) else ""
        deadline, delay = time.monotonic() + self._budget, 0.1
        while True:
            try:
                target = await self._resolve()
                response = await self._post(target.url, line, self._headers(target.token))
            except SharedServerError as exc:
                return _error(rid, _UNAVAILABLE, str(exc))
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never sent: always safe to resend
                reason = f"unreachable ({type(exc).__name__})"
            except httpx.TransportError as exc:  # sent, then lost: the call may have applied
                if method not in _READ_ONLY:
                    return _error(rid, _UNKNOWN_OUTCOME, self._lost(method, exc))
                reason = f"disconnected ({type(exc).__name__})"
            else:
                code = response.status_code
                if code == 202:
                    return None
                if code == 200:
                    return response.content.strip()
                door_refusal = code == 503 and (DRAINING_HEADER in response.headers or BUSY_HEADER in response.headers)
                if not (door_refusal or code == 401):  # 401: the token may have rotated; it is re-read each try
                    return _error(rid, _UNAVAILABLE, f"shared trw-mcp answered HTTP {code}: {response.text[:300]}")
                reason = _refusal(response)
            if time.monotonic() + delay > deadline:
                return _error(rid, _UNAVAILABLE, self._gave_up(reason))
            await self._sleep(delay)
            delay = min(delay * 2, 2.0)

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {token}",
            SESSION_HEADER: self._identity,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }

    def _lost(self, method: str, exc: Exception) -> str:
        return (
            f"the shared trw-mcp ({self._env}) connection was lost after {method or 'the request'} was sent "
            f"({type(exc).__name__}); the call may or may not have applied. Check its effect, then retry once "
            f"with the same arguments. Diagnose with `trw-mcp status --shared`."
        )

    def _gave_up(self, reason: str) -> str:
        return (
            f"the shared trw-mcp env {self._env!r} stayed {reason} for {self._budget:.0f}s; the request was NOT "
            f"applied. Run `trw-mcp status --shared` and read .trw/runtime/shared-mcp/{self._env}.log; "
            f"`shared_mcp: {{enabled: false}}` in .trw/config.yaml reverts every proxy to per-client stdio."
        )


def _refusal(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", response.status_code))
    except ValueError:
        return f"HTTP {response.status_code}"


def _error(rid: Any, code: int, message: str) -> bytes | None:
    if rid is None:  # a notification cannot be answered; say so where the operator can see it
        print(f"trw-mcp-proxy: {message}", file=sys.stderr, flush=True)
        return None
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}).encode()


class EnvResolver:
    """Reads the env's record on every call; starts its server (once, under a lock) when absent."""

    def __init__(self, paths: SharedPaths, env: str, *, project_root: str, start_seconds: float = 90.0) -> None:
        self._paths, self._env, self._root, self._start_seconds = paths, env, project_root, start_seconds

    async def __call__(self) -> Target:
        info = read_live_record(self._paths, self._env)
        if info is None:
            info = await asyncio.to_thread(self._start)
        return Target(url=info.url, token=read_token(self._paths))

    def _start(self) -> DaemonInfo:
        from trw_mcp._locking import _lock_ex, _lock_un

        self._paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self._paths.root / f"{self._env}.spawn.lock", "a") as lock:
            _lock_ex(lock.fileno())
            try:
                info = read_live_record(self._paths, self._env)
                if info is not None:
                    return info
                proc = spawn_server(self._paths, self._env, project_root=self._root)
                info = wait_published(self._paths, self._env, proc, seconds=self._start_seconds)
                record_serving_env(self._paths, self._env, os.environ)  # only a confirmed start pins its environment
                return info
            finally:
                _lock_un(lock.fileno())


def spawn_server(paths: SharedPaths, env: str, *, project_root: str, successor: bool = False) -> Any:
    """Start *env*'s server detached from this process; its output goes to ``<env>.log``."""
    python = env_python(paths, env)
    # trw:intentional the server stands in for the stdio server the client would have launched, so it starts
    # from the launcher's full environment (API keys, TRW_* config); a swap successor instead replays the
    # allowlisted keys recorded at the env's first start. Client identity is removed by the server itself.
    child_env = {**os.environ}
    recorded = env_serving_env(paths, env)
    if successor and recorded is not None:  # a swap keeps the env's first-start environment, not the swapper's shell
        differing = sorted(k for k in recorded if child_env.get(k) != recorded[k])
        if differing:  # key names only, never values; a stale recorded key is cleared by deleting <env>.serving-env
            print(
                f"trw-mcp: swap successor of {env!r} replays recorded env keys {differing}", file=sys.stderr, flush=True
            )
        for key in serving_env_keys(child_env):
            del child_env[key]
        child_env.update(recorded)
    child_env["TRW_PROJECT_ROOT"] = project_root
    user_dir = paths.user_dir(env)
    if user_dir is not None:
        if not (user_dir / "memory").is_dir():
            raise SharedServerError(f"env {env!r} has no memory dir at {user_dir}; run `trw-mcp env create {env}`")
        child_env["TRW_USER_DIR"] = str(user_dir)
    # PYTHONPATH/PYTHONHOME belong to the env's swap record alone: never the launcher's or swapper's shell, or a
    # --version venv would serve the shell's worktree source instead of its own site-packages.
    child_env.pop("PYTHONPATH", None)
    child_env.pop("PYTHONHOME", None)
    pythonpath = env_pythonpath(paths, env)
    if pythonpath:  # a `swap --src` worktree: its source
        child_env["PYTHONPATH"] = pythonpath
    argv = [python, "-m", "trw_mcp.server", "serve", "--shared", "--env", env, *(["--successor"] if successor else [])]
    with open(paths.log(env), "ab") as log:
        proc = subprocess.Popen(  # noqa: S603 -- fixed argv; the interpreter comes from the operator's swap
            argv,
            cwd=project_root,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    # Reap it whenever it exits: an unreaped child stays a zombie that answers kill(pid, 0) for as long as
    # this (long-lived) proxy runs, so a drained server would look alive to every liveness check.
    threading.Thread(target=proc.wait, name=f"reap-shared-{env}", daemon=True).start()
    return proc


def wait_published(
    paths: SharedPaths, env: str, proc: Any, *, seconds: float, not_pid: int | None = None
) -> DaemonInfo:
    """The live record *proc* publishes (naming a pid other than *not_pid*), or a refusal naming the log."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        info = read_live_record(paths, env)
        if info is not None and info.pid != not_pid:
            return info
        if proc.poll() is not None:
            raise SharedServerError(
                f"the shared trw-mcp for {env!r} exited ({proc.returncode}) before serving; see {paths.log(env)}"
            )
        time.sleep(0.1)
    raise SharedServerError(
        f"the shared trw-mcp for {env!r} did not publish within {seconds:.0f}s; see {paths.log(env)}"
    )


async def pump(proxy: Proxy, reader: asyncio.StreamReader, write: Callable[[bytes], None]) -> None:
    """Forward every stdin line concurrently (a long tool call never blocks a ping) until EOF."""
    pending: set[asyncio.Task[None]] = set()

    async def one(line: bytes) -> None:
        answer = await proxy.forward(line)
        if answer is not None:
            write(answer + b"\n")

    while line := await reader.readline():
        if line.strip():
            task = asyncio.create_task(one(line))
            pending.add(task)
            task.add_done_callback(pending.discard)
    if pending:
        await asyncio.wait(pending, timeout=5.0)


def proxy_paths() -> tuple[SharedPaths, Path]:
    """(paths, project root) of the shared records this proxy uses: the same resolver as swap and status.

    PROXY-PROJECT-ROOT: the proxy used ``resolve_trw_dir()`` while the CLI used the git toplevel, so from a
    subdirectory a swap wrote one record and the proxy's server was found through another.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.shared_server._cli import shared_project_root
    from trw_mcp.state._project_root_binding import project_bound

    root = shared_project_root()
    with project_bound(root):
        config = get_config()
    return SharedPaths.resolve(root / str(config.trw_dir), config.shared_mcp), root


def run_proxy(env: str) -> None:
    """Serve stdio for one client session against *env*'s shared server."""
    paths, root = proxy_paths()
    resolver = EnvResolver(paths, env, project_root=str(root))

    async def main() -> None:
        reader = asyncio.StreamReader(limit=_MAX_LINE)
        await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

        def write(data: bytes) -> None:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

        timeout = httpx.Timeout(900.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, limits=httpx.Limits(max_connections=8)) as http:

            async def post(url: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
                return await http.post(url, content=body, headers=headers)

            await pump(Proxy(resolve=resolver, post=post, identity=client_identity(), env=env), reader, write)

    asyncio.run(main())
