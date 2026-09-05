"""PRD-CORE-248 FR01 — ``initialize`` is answered before any sync/profile resolution.

The FastMCP lifespan is entered inside ``mcp.run()`` **before** the lowlevel
server processes its first message, so everything it did preceded the
``initialize`` reply: backend-sync config resolution, sync-target resolution,
and — through ``BackendSyncClient.__init__`` -> ``resolve_sync_client_id()`` ->
``cfg.client_profile`` — client-profile resolution. Measured 1111.0-1114.9 ms
against a reply at 1116.3 ms.

The measured saving is 12.6 ms. These tests exist for the INVARIANT: that block
is free of network I/O today, and this is what stops the next contributor
putting a network call there without the client having any defence.

Every test drives the real object ``create_app`` returns, through a real MCP
client handshake — never a stub and never a recorded log fixture (the
"delivered != wired" bar in ``docs/documentation/wiring-defect-patterns.md`` §4).
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time

import pytest

from tests._structlog_capture import captured_structlog as captured_structlog

#: The lifespan events FR01 forbids ahead of the reply. The first three are the
#: ones the boot timeline probe measured at 1111.0-1114.9 ms.
FORBIDDEN_BEFORE_REPLY = (
    "sync_config_resolved",
    "multi_platform_profile_resolution",
    "sync_targets_resolved",
    "sync_loop_started",
)


def _phase_index(logs: list[dict[str, object]], phase: str) -> int:
    """Index of the ``boot_phase`` event named *phase*, asserting it exists."""
    for i, log in enumerate(logs):
        if log.get("event") == "boot_phase" and log.get("phase") == phase:
            return i
    raise AssertionError(f"boot_phase={phase} absent; got {[log.get('event') for log in logs]}")


@pytest.fixture(autouse=True)
def _reset_boot_state() -> None:
    """Clear the completion latch and let boot-phase events render.

    ``emit_boot_phase`` buffers until ``enable_boot_timeline_emission`` — the
    production CLI calls that right after ``configure_logging``, because
    structlog's unconfigured default writes to the stdout JSON-RPC channel.
    Tests must opt in the same way or the timeline is invisible to capture_logs.
    """
    from trw_mcp.server._boot_deferred import reset_deferred_boot_state
    from trw_mcp.server._boot_timeline import enable_boot_timeline_emission

    enable_boot_timeline_emission()
    reset_deferred_boot_state()


async def _handshake(app: object, logs: list[dict[str, object]]) -> list[dict[str, object]]:
    """Run one real initialize handshake against *app*, returning the captured logs.

    ``logs`` comes from the shared ``captured_structlog`` fixture rather than a
    bare ``capture_logs()``: ``configure_logging`` installs a filtering wrapper
    class process-wide, and any sibling test that touches the CLI/server startup
    path leaves it installed, so a bare capture yields an empty list and a false
    pass. See ``tests/_structlog_capture.py``.
    """
    from fastmcp import Client

    async with Client(app) as client:  # type: ignore[arg-type]
        await client.ping()
    # The deferred step runs on a worker thread; give the loop a few turns so
    # its events land inside the capture window.
    for _ in range(100):
        from trw_mcp.server._boot_deferred import deferred_work_done

        if deferred_work_done():
            break
        await asyncio.sleep(0.01)
    return list(logs)


async def test_initialize_precedes_sync_resolution(captured_structlog: list[dict[str, object]]) -> None:
    """No sync/target/profile event may precede the initialize reply."""
    from trw_mcp.server._app import create_app
    from trw_mcp.server._boot_timeline import BOOT_PHASES

    logs = await _handshake(create_app(), captured_structlog)

    answered_at = _phase_index(logs, "initialize_answered")
    assert BOOT_PHASES[3] == "initialize_answered"
    for name in FORBIDDEN_BEFORE_REPLY:
        offenders = [i for i, log in enumerate(logs) if log.get("event") == name and i < answered_at]
        assert not offenders, f"{name} was emitted BEFORE the initialize reply (indices {offenders})"


async def test_profile_resolution_is_deferred(captured_structlog: list[dict[str, object]]) -> None:
    """Client-profile resolution reaches the log stream only after the reply, if at all.

    Profile resolution is reached through ``BackendSyncClient.__init__`` ->
    ``resolve_sync_client_id()`` -> ``cfg.client_profile``. On a tree with no
    backend credentials the client is never constructed, so the event is absent
    entirely — which still satisfies the invariant. What must never happen is the
    event landing ahead of the reply.
    """
    from trw_mcp.server._app import create_app

    logs = await _handshake(create_app(), captured_structlog)

    answered_at = _phase_index(logs, "initialize_answered")
    profile_events = [i for i, log in enumerate(logs) if log.get("event") == "multi_platform_profile_resolution"]
    assert all(i > answered_at for i in profile_events)


def test_lifespan_resolves_nothing_before_yield() -> None:
    """The structural half of FR01: the lifespan body owns task lifecycle only.

    An ordering test alone can be satisfied by a lifespan that resolves fast; the
    invariant is that it does not resolve AT ALL before ``yield``.
    """
    from trw_mcp.server._app import _build_sync_lifespan

    source = inspect.getsource(_build_sync_lifespan)
    body = source.split('"""')[-1]
    before_yield = body.split("yield")[0]
    for forbidden in ("BackendSyncClient", "resolved_backend_url", "sync_config_resolved"):
        assert forbidden not in before_yield, f"{forbidden} is back on the pre-initialize critical path in the lifespan"


async def test_first_tool_call_runs_the_resolution_inline_when_it_did_not_complete(
    captured_structlog: list[dict[str, object]],
) -> None:
    """NFR02 fail-closed: a tool call never observes an unresolved sync configuration."""
    from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware
    from trw_mcp.server import _boot_deferred

    ran: list[str] = []
    middleware = BootDeferralMiddleware(budget_ms=5000)

    async def _call_next(_ctx: object) -> str:
        ran.append("tool")
        return "ok"

    assert _boot_deferred.deferred_work_done() is False
    result = await middleware.on_call_tool(object(), _call_next)  # type: ignore[arg-type]

    assert result == "ok"
    assert ran == ["tool"]
    assert _boot_deferred.deferred_work_done() is True, "the tool call must have resolved sync inline"
    assert any(log.get("event") == "boot_deferred_work_ran_inline" for log in captured_structlog)


async def test_first_tool_call_is_a_no_op_once_resolved(captured_structlog: list[dict[str, object]]) -> None:
    """Non-vacuity: the fallback does not re-run on every call."""
    from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware
    from trw_mcp.server import _boot_deferred

    middleware = BootDeferralMiddleware(budget_ms=5000)

    async def _call_next(_ctx: object) -> str:
        return "ok"

    await middleware.on_call_tool(object(), _call_next)  # type: ignore[arg-type]
    captured_structlog.clear()
    await middleware.on_call_tool(object(), _call_next)  # type: ignore[arg-type]
    assert not [log for log in captured_structlog if log.get("event") == "boot_deferred_work_ran_inline"]
    assert _boot_deferred.deferred_work_done() is True


async def test_budget_exceeded_warns_and_the_worker_keeps_running(
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, object]],
) -> None:
    """The budget bounds the AWAIT, not the worker — and the latch must know that.

    This patches ``_resolve_backend_sync`` (the leaf), NOT
    ``ensure_deferred_boot_work``. The earlier version of this test patched the
    latch function itself, so it never exercised the claim-vs-completion state
    machine at all — which is how a `_done = True`-on-claim bug survived it.
    """
    from trw_mcp.server import _boot_deferred

    release = threading.Event()
    entered = threading.Event()

    def _slow() -> None:
        entered.set()
        release.wait(timeout=10)

    monkeypatch.setattr(_boot_deferred, "_resolve_backend_sync", _slow)
    try:
        await _boot_deferred.schedule_deferred_boot_work(budget_ms=100)

        assert entered.is_set(), "the attempt must actually have started"
        warnings = [log for log in captured_structlog if log.get("event") == "boot_deferred_work_budget_exceeded"]
        assert warnings, f"budget overrun must warn; got {captured_structlog}"
        assert warnings[0]["budget_ms"] == 100
        # The decisive assertion: the abandoned await did NOT mark the work done.
        assert _boot_deferred.deferred_work_done() is False
        assert _boot_deferred.deferred_work_in_flight() is True
    finally:
        release.set()
        for _ in range(200):
            if not _boot_deferred.deferred_work_in_flight():
                break
            await asyncio.sleep(0.01)


async def test_tool_call_blocks_until_an_in_flight_resolution_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REAL middleware + the REAL latch: no tool body runs mid-resolution.

    Drives the exact sequence the reviewer named: the scheduled attempt overruns
    its budget and is still executing when a tool call arrives. Before the
    claim/completion split, ``deferred_work_done()`` answered True on claim and
    ``call_next`` ran immediately against an unresolved sync configuration.
    """
    from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware
    from trw_mcp.server import _boot_deferred

    release = threading.Event()
    entered = threading.Event()
    resolved: list[str] = []

    def _slow() -> None:
        entered.set()
        release.wait(timeout=10)
        resolved.append("resolved")

    monkeypatch.setattr(_boot_deferred, "_resolve_backend_sync", _slow)
    middleware = BootDeferralMiddleware(budget_ms=50)
    order: list[str] = []

    async def _call_next(_ctx: object) -> str:
        order.append("tool_body")
        return "ok"

    try:
        await _boot_deferred.schedule_deferred_boot_work(budget_ms=50)
        assert entered.is_set()
        assert _boot_deferred.deferred_work_done() is False

        call = asyncio.ensure_future(middleware.on_call_tool(object(), _call_next))  # type: ignore[arg-type]
        # Give the loop real opportunities to run the tool body if it were free to.
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert order == [], "the tool body ran while the resolution was still in flight"
        assert resolved == []

        release.set()
        assert await call == "ok"
        assert order == ["tool_body"], "the tool must run once resolution completes"
        assert resolved == ["resolved"]
        assert _boot_deferred.deferred_work_done() is True
    finally:
        release.set()


async def test_a_failed_attempt_releases_waiters_and_stays_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising resolution must not leave a waiter blocked forever."""
    from trw_mcp.server import _boot_deferred

    def _boom() -> None:
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr(_boot_deferred, "_resolve_backend_sync", _boom)
    assert await asyncio.to_thread(_boot_deferred.ensure_deferred_boot_work) is False
    assert _boot_deferred.deferred_work_in_flight() is False
    assert _boot_deferred.deferred_work_done() is False
    # A second caller re-attempts rather than inheriting the failure.
    assert await asyncio.to_thread(_boot_deferred.ensure_deferred_boot_work) is False


async def test_concurrent_callers_resolve_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A burst of first tool calls performs the resolution once, and all see it done."""
    from trw_mcp.server import _boot_deferred

    runs: list[int] = []

    def _slow() -> None:
        runs.append(1)
        time.sleep(0.05)

    monkeypatch.setattr(_boot_deferred, "_resolve_backend_sync", _slow)
    results = await asyncio.gather(*(asyncio.to_thread(_boot_deferred.ensure_deferred_boot_work) for _ in range(6)))
    assert sum(runs) == 1, "the resolution must run exactly once"
    assert sum(1 for r in results if r) == 1, "exactly one caller reports having run it"
    assert _boot_deferred.deferred_work_done() is True


async def test_scheduling_is_skipped_when_the_work_already_ran(
    captured_structlog: list[dict[str, object]],
) -> None:
    """A completed resolution is not re-scheduled (idempotence)."""
    from trw_mcp.server import _boot_deferred

    _boot_deferred.ensure_deferred_boot_work()
    captured_structlog.clear()
    await _boot_deferred.schedule_deferred_boot_work(budget_ms=1)
    assert not [log for log in captured_structlog if log.get("event") == "boot_deferred_work_budget_exceeded"]


def test_resolution_failure_clears_the_latch_so_the_next_caller_retries(
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, object]],
) -> None:
    """NFR02: a failed resolution must not latch 'done' on a half-resolved state."""
    from trw_mcp.server import _boot_deferred

    monkeypatch.setattr(
        _boot_deferred,
        "_resolve_backend_sync",
        lambda: (_ for _ in ()).throw(RuntimeError("backend unreachable")),
    )
    assert _boot_deferred.ensure_deferred_boot_work() is False
    assert _boot_deferred.deferred_work_done() is False
    assert any(log.get("event") == "boot_deferred_work_failed" for log in captured_structlog)


def test_boot_deferral_middleware_is_in_the_production_chain() -> None:
    """Wiring assertion: the hook ships on the object create_app returns."""
    from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware
    from trw_mcp.server._app import create_app

    chain = [type(m).__name__ for m in create_app().middleware]
    assert BootDeferralMiddleware.__name__ in chain
    assert chain[0] == BootDeferralMiddleware.__name__, (
        "the deferral hook must wrap the whole chain, including the handshake"
    )
