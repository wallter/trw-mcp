"""PRD-FIX-087 FR04: sync's slow backend calls do NOT block the asyncio event loop.

Pre-fix: sync/pull.py and sync/push.py used synchronous httpx.Client. When
called from async run_sync_loop, a 5s ReadTimeout (or any slow response)
froze the asyncio event loop for the entire duration, hanging in-flight
MCP tool calls in the same process.

Post-fix: httpx.AsyncClient + await yields the event loop on every I/O.
A concurrent fast coroutine can complete while a 5s slow handler is in
flight.

This test is the durable regression guard. If a future change reverts
to sync httpx (or wraps a sync call in a coroutine without yielding),
this test fails loudly.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


def _slow_then_concurrent_handler(delay: float) -> Any:
    """Build an async httpx.MockTransport handler that sleeps before responding.

    Used to simulate a slow backend so we can prove the event loop is not
    blocked: a parallel fast coroutine should complete BEFORE this handler
    finishes its sleep.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return httpx.Response(200, json={"etag": "x", "sync_hints": {}, "team_learnings": []})

    return httpx.MockTransport(handler)


async def _fast_coroutine_with_external_start(start: float) -> float:
    """Returns wall-clock seconds since ``start`` (a time.monotonic() snapshot).

    Captures the *external* start time, so the elapsed measurement
    includes scheduling delay caused by event-loop blocking. With a
    non-blocking loop the fast coroutine is scheduled and completes
    almost immediately; with a blocked loop it doesn't run until the
    blocker releases.
    """

    await asyncio.sleep(0)  # one event loop tick
    return time.monotonic() - start


async def test_pull_does_not_block_concurrent_coroutine() -> None:
    """A 1s slow pull must not stall a parallel fast coroutine for the full 1s."""
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(
        backend_url="http://test.invalid",
        api_key="test",
        timeout=2.0,
        client_id="sync-test",
    )

    # Slow handler: sleeps 1s before responding 200.
    slow_transport = _slow_then_concurrent_handler(delay=1.0)

    # Patch httpx.AsyncClient so SyncPuller's internal client uses our slow transport.
    real_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = slow_transport
        return real_async_client(*args, **kwargs)

    import unittest.mock

    with unittest.mock.patch("httpx.AsyncClient", patched_async_client):
        # Race the slow pull against the fast coroutine.
        start = time.monotonic()
        results = await asyncio.gather(
            puller.pull_intel_state(),
            _fast_coroutine_with_external_start(start),
        )
        elapsed = time.monotonic() - start

    # The slow coroutine took ~1s; the fast coroutine — measured from the
    # gather start — must be <300ms because the event loop yielded during
    # the slow pull's await. With sync httpx (pre-fix), the fast coro
    # would have been delayed the full ~1s.
    fast_elapsed = float(results[1])
    assert fast_elapsed < 0.3, (
        f"Fast coroutine took {fast_elapsed*1000:.1f}ms (cap 300ms) — event loop was blocked "
        f"by sync pull (regression to sync httpx). With AsyncClient + await, "
        f"the fast coro should complete almost immediately while the slow pull "
        f"awaits."
    )
    # Sanity: the gather still took ~1s overall (slow pull dominates).
    assert elapsed >= 0.9, (
        f"Total gather time {elapsed*1000:.1f}ms — slow handler did not actually "
        f"sleep for 1s as configured."
    )


async def test_push_does_not_block_concurrent_coroutine() -> None:
    """A 1s slow push must not stall a parallel fast coroutine for the full 1s."""
    from unittest.mock import MagicMock

    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(
        backend_url="http://test.invalid",
        api_key="test",
        timeout=2.0,
        batch_size=1,
        client_id="sync-test",
    )

    async def slow_post(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"inserted": 1, "updated": 0, "skipped": 0})

    slow_transport = httpx.MockTransport(slow_post)
    real_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = slow_transport
        return real_async_client(*args, **kwargs)

    import unittest.mock

    entry = MagicMock(spec=MemoryEntry)
    entry.id = "L-conc"
    entry.sync_hash = "h"
    entry.sync_seq = 1
    entry.to_dict.return_value = {
        "id": "L-conc",
        "sync_hash": "h",
        "sync_seq": 1,
        "summary": "test",
        "detail": None,
        "importance": 0.5,
        "tags": [],
        "type": "pattern",
        "status": "active",
        "vector_clock": {},
        "metadata": {},
    }

    with unittest.mock.patch("httpx.AsyncClient", patched_async_client):
        start = time.monotonic()
        results = await asyncio.gather(
            pusher.push_learnings([entry]),
            _fast_coroutine_with_external_start(start),
        )
        elapsed = time.monotonic() - start

    fast_elapsed = float(results[1])
    assert fast_elapsed < 0.3, (
        f"Fast coroutine took {fast_elapsed*1000:.1f}ms (cap 300ms) — event loop was blocked "
        f"by sync push. AsyncClient + await must yield."
    )
    assert elapsed >= 0.9


async def test_negative_control_sync_httpx_would_block_event_loop() -> None:
    """Negative control: prove the FR04 mechanic actually catches blocking.

    Calls a synchronous time.sleep inside an async function (simulating
    pre-fix sync httpx behavior) and asserts the fast coroutine is
    blocked. If this test ever PASSES with `assert fast_elapsed < 0.1`,
    the test mechanic is broken (it would silently fail to catch a
    real regression).
    """

    async def blocking_call() -> None:
        # Simulates what sync httpx.Client.get(...) does: blocks the
        # current thread without yielding to the event loop.
        time.sleep(0.5)  # noqa: ASYNC251 - intentional negative-control blocking call.

    start = time.monotonic()
    results = await asyncio.gather(
        blocking_call(),
        _fast_coroutine_with_external_start(start),
    )
    fast_elapsed = float(results[1])

    # The fast coroutine measures wall-clock time since gather start.
    # If the loop was blocked during blocking_call's 0.5s sleep, the fast
    # coroutine couldn't run until after the block released, so its
    # wall-clock elapsed should be >= 0.4s.
    assert fast_elapsed >= 0.4, (
        f"Negative control: fast coroutine completed in {fast_elapsed*1000:.1f}ms "
        f"despite a blocking time.sleep(0.5). The test mechanic is broken — "
        f"the positive tests above cannot be trusted."
    )
