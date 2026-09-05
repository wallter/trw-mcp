"""PRD-CORE-248 NFR02 — a failed inline resolution says so.

``ensure_deferred_boot_work`` returns ``False`` for two opposite outcomes:
another caller's attempt completed while this one waited, or THIS attempt ran
and raised, leaving the completion latch clear. The middleware logged both as
``boot_deferred_work_awaited``, so the only externally visible difference
between "the configuration is resolved" and "the tool call is about to proceed
against an unresolved configuration" was a warning emitted one frame deeper, in
a different module, under a name that reads like the scheduled path.

The fail-open behaviour itself is correct and is deliberately unchanged: a boot
fallback must never fail a tool call. What changes is that the operator can see
which of the two happened.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from tests._structlog_capture import captured_structlog as captured_structlog

_JOIN_TIMEOUT_SECONDS = 10.0


@pytest.fixture(autouse=True)
def _reset_boot_state() -> None:
    """Clear the completion latch so each test starts from an unresolved boot."""
    from trw_mcp.server._boot_deferred import reset_deferred_boot_state

    reset_deferred_boot_state()


def _events(logs: list[dict[str, object]]) -> list[object]:
    return [log.get("event") for log in logs]


async def test_a_failed_inline_resolution_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
) -> None:
    """The attempt runs here, raises, and the tool call still proceeds — loudly."""
    from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware
    from trw_mcp.server import _boot_deferred

    def _explode() -> None:
        raise RuntimeError("backend sync resolution blew up")

    monkeypatch.setattr(_boot_deferred, "_resolve_backend_sync", _explode)

    ran: list[str] = []

    async def _call_next(_ctx: object) -> str:
        ran.append("tool")
        return "ok"

    result = await BootDeferralMiddleware(budget_ms=5000).on_call_tool(object(), _call_next)  # type: ignore[arg-type]

    assert result == "ok" and ran == ["tool"], "NFR02 fail-open: a boot fallback never fails a tool call"
    assert _boot_deferred.deferred_work_done() is False
    events = _events(captured_structlog)
    assert "boot_deferred_work_failed_inline" in events, events
    assert "boot_deferred_work_awaited" not in events, "a failure was reported as a completed wait"
    assert "boot_deferred_work_ran_inline" not in events


async def test_waiting_for_another_callers_completed_attempt_is_still_awaited(
    monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
) -> None:
    """Non-vacuity: the "awaited" label survives, so the split is a real split.

    Produced the way it happens in production -- a second caller genuinely in
    flight -- rather than by setting the latch, because a pre-set latch would
    short-circuit the middleware before it ever waits.
    """
    from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware
    from trw_mcp.server import _boot_deferred

    release = threading.Event()
    claimed = threading.Event()
    waiting = threading.Event()

    def _blocking_resolve() -> None:
        claimed.set()
        assert release.wait(_JOIN_TIMEOUT_SECONDS), "the other caller was never released"

    real_wait = _boot_deferred._await_in_flight_attempt

    def _observed_wait() -> None:
        """Make "the middleware is now blocked" observable, so no sleep is needed."""
        waiting.set()
        real_wait()

    monkeypatch.setattr(_boot_deferred, "_resolve_backend_sync", _blocking_resolve)
    monkeypatch.setattr(_boot_deferred, "_await_in_flight_attempt", _observed_wait)

    other = threading.Thread(target=_boot_deferred.ensure_deferred_boot_work, daemon=True)
    other.start()
    assert claimed.wait(_JOIN_TIMEOUT_SECONDS), "the other caller never claimed the attempt"

    async def _call_next(_ctx: object) -> str:
        return "ok"

    middleware = BootDeferralMiddleware(budget_ms=5000)
    task = asyncio.create_task(middleware.on_call_tool(object(), _call_next))  # type: ignore[arg-type]
    assert await asyncio.to_thread(waiting.wait, _JOIN_TIMEOUT_SECONDS), "the tool call never blocked"
    release.set()
    result = await task
    other.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert result == "ok"
    assert _boot_deferred.deferred_work_done() is True
    events = _events(captured_structlog)
    assert "boot_deferred_work_awaited" in events, events
    assert "boot_deferred_work_failed_inline" not in events
