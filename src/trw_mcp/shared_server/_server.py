"""``trw-mcp serve --shared``: one detached trw-mcp per env on loopback streamable HTTP.

Stateless HTTP with plain JSON responses, as the trw-memory daemon serves: the
server holds no MCP session, so a restart or swap invalidates nothing a proxy
holds. Per-client identity rides the ``Mcp-Session-Id`` header the proxy sets
on every request; FastMCP's ``Context.session_id`` reads that header, so pin
keys and the ceremony middleware's per-session state stay per client and
survive a swap. The server's own environment is scrubbed of client identity
because ``resolve_pin_key`` consults the environment before the context.

The :class:`Door` in front of the app is the only admission point (bearer,
drain, in-flight cap). Each refusal happens before the app sees the request, so
the proxy may resend it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from typing import Any

import structlog
from trw_memory.daemon._drain_key import keys_match

from trw_mcp.shared_server._records import (
    BUSY_HEADER,
    DRAINING_HEADER,
    SESSION_HEADER,
    SharedPaths,
    SharedServerError,
    ensure_token,
    publish_record,
    read_live_record,
    withdraw_record,
)

logger = structlog.get_logger(__name__)

Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
_DRAIN_SECONDS = 300.0
_SESSION_WINDOW_SECONDS = 3600.0
#: Client identity the server must never carry itself: every client would inherit it.
_IDENTITY_ENV = ("TRW_SESSION_ID", "TRW_CLIENT_PROFILE", "TRW_AGENT_ID", "TRW_RUN_ID", "TRW_CHAIN_ID")


async def _reply(send: Send, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    raw = json.dumps(body).encode()
    pairs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    pairs += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": pairs})
    await send({"type": "http.response.body", "body": raw})


class Door:
    """ASGI admission in front of the MCP app; owns the drain and the activity clock."""

    def __init__(self, app: Any, *, token: str, env: str, version: str, max_inflight: int) -> None:
        self._app, self.token, self.env, self.version = app, token, env, version
        self.max_inflight = max_inflight
        self.in_flight = 0
        self.draining = False
        self.sessions: dict[str, float] = {}
        self._quiet = asyncio.Event()  # set when a drain finds nothing in flight
        self.started = self.last_activity = time.monotonic()
        self.on_drained: Callable[[], None] = lambda: None

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not keys_match(headers.get("authorization", "").removeprefix("Bearer "), self.token):
            await _reply(send, 401, {"error": "shared trw-mcp token rejected; connect through `trw-mcp-proxy`"})
        elif scope["path"] == "/admin/status":
            await _reply(send, 200, self.status())
        elif scope["path"] == "/admin/drain" and scope["method"] == "POST":
            self.begin_drain()
            await _reply(send, 202, {"draining": True, "pid": os.getpid()})
        elif (refusal := self.admit(headers.get(SESSION_HEADER, ""))) is not None:
            await _reply(send, refusal[0], {"error": refusal[1]}, refusal[2])
        else:
            self.in_flight += 1
            try:
                await self._app(scope, receive, send)
            finally:
                self.in_flight -= 1
                self.last_activity = time.monotonic()
                if self.draining and not self.in_flight:
                    self._quiet.set()

    def admit(self, session: str) -> tuple[int, str, dict[str, str]] | None:
        """``None`` admits; otherwise the (status, reason, headers) refusal. Never applied, so resendable."""
        if self.draining:
            return 503, "this shared trw-mcp is draining for a swap", {DRAINING_HEADER: "1"}
        if not session:
            return 400, "missing Mcp-Session-Id: connect through `trw-mcp-proxy`, which carries it", {}
        if self.in_flight >= self.max_inflight:
            busy = f"shared trw-mcp busy: {self.in_flight} requests in flight (shared_mcp.max_inflight)"
            return 503, busy, {BUSY_HEADER: "1"}
        self.sessions[session] = self.last_activity = time.monotonic()
        return None

    def status(self) -> dict[str, Any]:
        cutoff = time.monotonic() - _SESSION_WINDOW_SECONDS
        self.sessions = {sid: seen for sid, seen in self.sessions.items() if seen >= cutoff}
        return {
            "env": self.env,
            "version": self.version,
            "pid": os.getpid(),
            "uptime_seconds": round(time.monotonic() - self.started, 1),
            "sessions_last_hour": len(self.sessions),
            "in_flight": self.in_flight,
            "draining": self.draining,
        }

    def begin_drain(self) -> None:
        """Close the door; exit once the calls already admitted finish (bounded)."""
        if not self.draining:
            self.draining = True
            asyncio.get_running_loop().create_task(self._finish_drain())

    async def _finish_drain(self) -> None:
        if self.in_flight:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._quiet.wait(), _DRAIN_SECONDS)
        logger.info("shared_mcp_drained", abandoned=self.in_flight)
        self.on_drained()

    def idle_for(self, seconds: float) -> bool:
        return self.in_flight == 0 and time.monotonic() - self.last_activity >= seconds


@contextlib.contextmanager
def _claim_lock(paths: SharedPaths, env: str) -> Iterator[Callable[[], None]]:
    """Hold the env's claim lock; the yielded callable releases it early (right after the flip)."""
    from trw_mcp._locking import _lock_ex, _lock_un

    paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(paths.lock(env), "a") as handle:
        _lock_ex(handle.fileno())
        yield lambda: _lock_un(handle.fileno())


def prepare_process_env() -> None:
    """Refuse a reviewer-role server; drop client identity so no client inherits another's."""
    from trw_mcp.client_profiles.session_identity import known_session_id_env_vars

    if os.environ.get("TRW_SURFACE_ROLE"):
        raise SharedServerError("a shared server cannot carry TRW_SURFACE_ROLE; reviewer lanes use `trw-mcp serve`")
    for name in (*_IDENTITY_ENV, *known_session_id_env_vars()):
        os.environ.pop(name, None)


def serve_shared(*, env: str, successor: bool) -> None:
    """Claim *env*, serve until drained, idle, or signalled; withdraw the record on the way out."""
    from trw_memory.daemon._loopback import bind_loopback_socket

    from trw_mcp import __version__
    from trw_mcp.models.config import get_config
    from trw_mcp.server._app import build_served_app
    from trw_mcp.state._paths import resolve_trw_dir

    config = get_config()
    if not config.shared_mcp.enabled:
        raise SharedServerError("shared_mcp.enabled is false; set `shared_mcp: {enabled: true}` in .trw/config.yaml")
    if not config.ctx_isolation_enabled:
        raise SharedServerError("ctx_isolation_enabled is false, so every client would share one run pin")
    prepare_process_env()
    paths = SharedPaths.resolve(resolve_trw_dir(), config.shared_mcp)
    token = ensure_token(paths)
    with _claim_lock(paths, env) as release:
        prior = read_live_record(paths, env)
        if prior is not None and not successor:
            raise SharedServerError(
                f"env {env!r} is already served by pid {prior.pid} (v{prior.version}); use `trw-mcp swap --env {env}`"
            )
        sock = bind_loopback_socket(0)
        sock.listen(4096)  # the 64-entry default backlog drops connection bursts from many proxies
        host, port = sock.getsockname()[:2]
        app = build_served_app().http_app(transport="streamable-http", stateless_http=True, json_response=True)
        door = Door(app, token=token, env=env, version=__version__, max_inflight=config.shared_mcp.max_inflight)
        asyncio.run(_run(door, sock, paths, f"http://{host}:{port}/mcp", prior, release, config.shared_mcp))


async def _run(door: Door, sock: Any, paths: SharedPaths, url: str, prior: Any, release: Any, limits: Any) -> None:
    import httpx
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(door, log_config=None, lifespan="on", access_log=False))
    door.on_drained = lambda: setattr(server, "should_exit", True)
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if serving.done():
            await serving
            raise SharedServerError(f"the shared server for {door.env!r} exited before serving")
        await asyncio.sleep(0.02)
    publish_record(paths, door.env, url=url, version=door.version)
    release()
    logger.info("shared_mcp_serving", env=door.env, url=url, pid=os.getpid(), predecessor=getattr(prior, "pid", None))
    watchdog = asyncio.create_task(_watch_idle(door, server, paths, limits.idle_shutdown_seconds))
    try:
        if prior is not None:  # the flip is done: new calls reach us; the old one finishes its own and exits
            async with httpx.AsyncClient(timeout=10.0) as client:
                await drain_predecessor(client, prior, door.token)
        await serving
    finally:
        watchdog.cancel()
        withdraw_record(paths, door.env)


async def drain_predecessor(client: Any, prior: Any, token: str) -> bool:
    """Ask the replaced server to drain. A failure is reported and never ends this (already published) one."""
    import httpx

    try:
        answer = await client.post(
            prior.url.removesuffix("/mcp") + "/admin/drain", headers={"authorization": f"Bearer {token}"}
        )
        answer.raise_for_status()
    except httpx.HTTPError as exc:  # trw-fail-silent-allow: logged; the old server still exits on idle
        logger.warning("shared_mcp_predecessor_drain_failed", pid=prior.pid, error=f"{type(exc).__name__}: {exc}")
        return False
    return True


async def _watch_idle(door: Door, server: Any, paths: SharedPaths, idle_seconds: int) -> None:
    """Exit after *idle_seconds* without a request; the record goes first so proxies start a fresh one."""
    while idle_seconds and not server.should_exit:
        await asyncio.sleep(min(30.0, idle_seconds / 10))
        if door.idle_for(idle_seconds):
            logger.info("shared_mcp_idle_shutdown", idle_seconds=idle_seconds)
            withdraw_record(paths, door.env)
            door.draining = True
            server.should_exit = True
