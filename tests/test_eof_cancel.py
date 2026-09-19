"""PRD-CORE-274 Amendment 01 (FR11): stdio-EOF cancellation adapter.

The SDK ``ClientSession`` drives a real FastMCP low-level server over hand-made
memory streams, so the client→server stream can be closed on purpose (a genuine
``EndOfStream`` at the server) WITHOUT anything cancelling the server task from
outside — the memory transport of ``fastmcp.Client`` cancels the server task on
exit and would mask exactly the defect under test.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest
from anyio.abc import TaskGroup
from fastmcp import FastMCP
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.client.session import ClientSession
from mcp.server.lowlevel.server import Server as SdkServer

from trw_mcp.server._app import create_app
from trw_mcp.server._eof_cancel import (
    _EOFSignallingReceiveStream,
    _SessionCapture,
    install_eof_cancel,
)

LINGER = 6.0  # long enough that a joined handler is unmistakable; never reached when cancelled
PROMPT = 3.0  # generous upper bound for "cancelled promptly" on a loaded machine


@dataclass
class Probe:
    started: anyio.Event = field(default_factory=anyio.Event)
    cancelled: bool = False
    finished: bool = False
    lifespan_exited: bool = False
    #: Ordered teardown markers: the low-level lifespan must complete an async
    #: checkpoint during teardown BEFORE run() returns; a poisoned outer scope
    #: would raise at that checkpoint and the marker would never be recorded.
    events: list[str] = field(default_factory=list)


def _app(probe: Probe, *, install: bool = True) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            probe.lifespan_exited = True

    app = FastMCP("eof-test", lifespan=lifespan)

    @app.tool()
    async def linger(seconds: float) -> str:
        probe.started.set()
        try:
            await anyio.sleep(seconds)
        except anyio.get_cancelled_exc_class():
            probe.cancelled = True
            raise
        probe.finished = True
        return "done"

    @app.tool()
    def ping() -> str:
        return "pong"

    if install:
        assert install_eof_cancel(app) is True
    _instrument_low_level_lifespan(app, probe)
    return app


def _instrument_low_level_lifespan(app: FastMCP, probe: Probe) -> None:
    """Wrap the LOW-LEVEL server lifespan (the one run() enters) with a real await in teardown."""
    low: LowLevelServer[Any, Any] = app._mcp_server
    original = low.lifespan

    @asynccontextmanager
    async def instrumented(server: Any) -> AsyncIterator[Any]:
        async with original(server) as ctx:
            try:
                yield ctx
            finally:
                await anyio.lowlevel.checkpoint()  # genuine async checkpoint during teardown
                probe.events.append("low_level_lifespan_teardown_after_checkpoint")

    low.lifespan = instrumented


@dataclass
class Served:
    session: ClientSession
    close_client_to_server: Any
    run_done: anyio.Event


@asynccontextmanager
async def _serve(app: FastMCP, *, initialize: bool = True, probe: Probe | None = None) -> AsyncIterator[Served]:
    c2s_send, c2s_recv = anyio.create_memory_object_stream[Any](0)
    s2c_send, s2c_recv = anyio.create_memory_object_stream[Any](0)
    run_done = anyio.Event()
    low: LowLevelServer[Any, Any] = app._mcp_server

    # FastMCP 3.x requires its own lifespan manager to be entered before the low-level
    # run (what run_stdio_async does at fastmcp/server/mixins/transport.py:238).
    async with app._lifespan_manager(), anyio.create_task_group() as tg:

        async def run_server() -> None:
            try:
                await low.run(c2s_recv, s2c_send, low.create_initialization_options())
            finally:
                if probe is not None:
                    probe.events.append("run_returned")
                run_done.set()

        tg.start_soon(run_server)
        async with ClientSession(s2c_recv, c2s_send) as session:
            if initialize:
                await session.initialize()
            yield Served(session, c2s_send.aclose, run_done)
        tg.cancel_scope.cancel()  # tests assert run_done BEFORE this point; this only reaps stragglers


async def _start_linger(tg: TaskGroup, served: Served, seconds: float = LINGER) -> None:
    async def call() -> None:
        with contextlib.suppress(Exception):  # the connection ends underneath the call
            await served.session.call_tool("linger", {"seconds": seconds})

    tg.start_soon(call)


async def _wait(event: anyio.Event, within: float = PROMPT) -> None:
    with anyio.fail_after(within):
        await event.wait()


# ── proxy ───────────────────────────────────────────────────────────


async def test_proxy_passes_items_through_and_fires_eof_exactly_once() -> None:
    send, recv = anyio.create_memory_object_stream[int](2)
    fired: list[int] = []
    proxy: _EOFSignallingReceiveStream[int] = _EOFSignallingReceiveStream(recv, lambda: fired.append(1))
    await send.send(1)
    await send.send(2)
    await send.aclose()
    async with proxy:  # the SDK enters the stream as a context manager and iterates it
        assert [item async for item in proxy] == [1, 2]  # iteration ends on the genuine EndOfStream
        assert fired == [1]
        for _ in range(3):  # a closed stream polled again re-raises EndOfStream; the callback stays at one
            with pytest.raises(anyio.EndOfStream):
                await proxy.receive()
        assert fired == [1]
    with pytest.raises(anyio.ClosedResourceError):  # aclose delegated: the inner stream is closed
        await recv.receive()


async def test_proxy_reraises_non_eof_errors_without_firing() -> None:
    class Broken:
        async def receive(self) -> int:
            raise ValueError("not an EOF")

        async def aclose(self) -> None:
            return None

    fired: list[int] = []
    proxy: _EOFSignallingReceiveStream[int] = _EOFSignallingReceiveStream(Broken(), lambda: fired.append(1))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not an EOF"):
        await proxy.receive()
    assert fired == []


# ── installation ────────────────────────────────────────────────────


def test_install_is_idempotent_and_puts_capture_first() -> None:
    app = FastMCP("t")
    assert install_eof_cancel(app) is True
    assert install_eof_cancel(app) is True
    assert isinstance(app.middleware[0], _SessionCapture)
    assert sum(isinstance(m, _SessionCapture) for m in app.middleware) == 1
    assert "run" in vars(app._mcp_server)  # instance shadow, not a class patch


def test_install_is_a_no_op_when_fastmcp_inherits_the_sdk_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LowLevelServer, "run", SdkServer.run)
    app = FastMCP("t")
    assert install_eof_cancel(app) is False
    assert not any(isinstance(m, _SessionCapture) for m in app.middleware)
    assert "run" not in vars(app._mcp_server)


def test_install_refuses_an_overriding_run_outside_the_verified_range() -> None:
    app = FastMCP("t")
    with pytest.raises(RuntimeError, match=r">=3\.2\.0,<4\.0\.0"):
        install_eof_cancel(app, fastmcp_version="5.0.0")
    assert not any(isinstance(m, _SessionCapture) for m in app.middleware)


def test_production_factory_installs_the_adapter() -> None:
    app = create_app()
    assert getattr(app._mcp_server, "_trw_eof_cancel_installed", False) is True
    assert isinstance(app.middleware[0], _SessionCapture)
    assert "run" in vars(app._mcp_server)


# ── behaviour over a real session ───────────────────────────────────


async def test_control_without_adapter_joins_the_handler_after_eof() -> None:
    probe = Probe()
    app = _app(probe, install=False)
    async with _serve(app) as served, anyio.create_task_group() as tg:
        await _start_linger(tg, served, seconds=2.0)
        await _wait(probe.started)
        await served.close_client_to_server()
        with anyio.move_on_after(1.0):
            await served.run_done.wait()
        assert not served.run_done.is_set(), "without the adapter run() must still be joining the handler"
        await _wait(served.run_done, within=PROMPT + 2.0)
    assert probe.finished and not probe.cancelled


async def test_eof_cancels_the_in_flight_handler_and_run_returns() -> None:
    probe = Probe()
    app = _app(probe)
    async with _serve(app, probe=probe) as served, anyio.create_task_group() as tg:
        await _start_linger(tg, served)
        await _wait(probe.started)
        await served.close_client_to_server()
        await _wait(served.run_done)
    assert probe.cancelled and not probe.finished
    # Design-critical: only the handler task group was cancelled, so the low-level
    # lifespan teardown inside run() completed a real async checkpoint BEFORE run()
    # returned (no shielding, no external cancellation in this test).
    assert probe.events == ["low_level_lifespan_teardown_after_checkpoint", "run_returned"], probe.events
    assert probe.lifespan_exited, "the application lifespan teardown ran too"


async def test_eof_before_any_request_is_harmless() -> None:
    probe = Probe()
    app = _app(probe)
    async with _serve(app, initialize=False) as served:
        await served.close_client_to_server()
        await _wait(served.run_done)
    assert probe.lifespan_exited and not probe.cancelled


async def test_eof_while_a_later_middleware_blocks_the_first_tool_call() -> None:
    probe = Probe()
    app = _app(probe)
    blocked = anyio.Event()
    state = {"cancelled": False}

    class Blocking(Middleware):
        async def on_call_tool(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
            blocked.set()
            try:
                await anyio.sleep(LINGER)
            except anyio.get_cancelled_exc_class():
                state["cancelled"] = True
                raise
            return await call_next(context)

    app.add_middleware(Blocking())  # appended: runs AFTER the capture middleware
    async with _serve(app) as served, anyio.create_task_group() as tg:
        await _start_linger(tg, served)
        await _wait(blocked)
        await served.close_client_to_server()
        await _wait(served.run_done)
    assert state["cancelled"] and not probe.started.is_set()


async def test_overlapping_sessions_are_isolated() -> None:
    probe_a, probe_b = Probe(), Probe()
    app = FastMCP("shared")
    for name, probe in (("a", probe_a), ("b", probe_b)):
        _register_linger(app, name, probe)
    assert install_eof_cancel(app) is True
    async with _serve(app) as one, _serve(app) as two, anyio.create_task_group() as tg:
        tg.start_soon(_call_ignoring_close, one.session, "linger_a")
        tg.start_soon(_call_ignoring_close, two.session, "linger_b")
        await _wait(probe_a.started)
        await _wait(probe_b.started)
        await one.close_client_to_server()
        await _wait(one.run_done)
        assert probe_a.cancelled and not probe_b.cancelled
        assert (await two.session.list_tools()).tools, "the surviving session still answers"
        await two.close_client_to_server()
        await _wait(two.run_done)
    assert probe_b.cancelled


async def test_repeated_sessions_on_one_app_get_fresh_holders() -> None:
    probe = Probe()
    app = _app(probe)
    for _ in range(2):
        probe.started = anyio.Event()
        probe.cancelled = False
        async with _serve(app) as served, anyio.create_task_group() as tg:
            await _start_linger(tg, served)
            await _wait(probe.started)
            await served.close_client_to_server()
            await _wait(served.run_done)
        assert probe.cancelled
    assert sum(isinstance(m, _SessionCapture) for m in app.middleware) == 1


async def test_unsupported_topology_is_refused_before_handler_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.server.low_level import MiddlewareServerSession

    # A FastMCP that never exposes its handler task group: the assignment is swallowed.
    monkeypatch.setattr(
        MiddlewareServerSession,
        "_subscription_task_group",
        property(lambda self: None, lambda self, value: None),
        raising=False,
    )
    probe = Probe()
    app = _app(probe)
    async with _serve(app) as served:  # initialize succeeded: capture stores, does not validate
        result = await served.session.call_tool("linger", {"seconds": 0.1})
        assert result.isError, "the first handler-group request must be refused loudly"
        text = "".join(getattr(c, "text", "") for c in result.content)
        assert "no handler task group" in text, text  # the EOFCancelUnsupported message reaches the caller
        assert not probe.started.is_set(), "nothing downstream may run"
        await served.close_client_to_server()
        await _wait(served.run_done)


def _register_linger(app: FastMCP, name: str, probe: Probe) -> None:
    async def linger() -> str:
        probe.started.set()
        try:
            await anyio.sleep(LINGER)
        except anyio.get_cancelled_exc_class():
            probe.cancelled = True
            raise
        return "done"

    app.tool(linger, name=f"linger_{name}")


async def _call_ignoring_close(session: ClientSession, tool: str) -> None:
    with contextlib.suppress(Exception):
        await session.call_tool(tool, {})
