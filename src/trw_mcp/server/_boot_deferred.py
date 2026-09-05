"""Post-``initialize`` backend-sync resolution (PRD-CORE-248 FR01).

Parent facade: :mod:`trw_mcp.server._app`, whose lifespan used to do this work
inline. The FastMCP lifespan is entered inside ``mcp.run()`` **before** the
lowlevel server processes its first message, so everything it did preceded the
``initialize`` reply: backend-sync config resolution, sync-target resolution,
and — through ``BackendSyncClient.__init__`` -> ``resolve_sync_client_id()`` ->
``cfg.client_profile`` — client-profile resolution. Measured 1111.0-1114.9 ms
against a reply at 1116.3 ms.

The measured saving is 12.6 ms. This exists for the **invariant**, not the
constant: that block does only URL/key lookups and identity resolution today, so
the cost is small, but nothing stopped the next contributor from putting a
network call there and the client had no defence if they did. The ordering
contract test is what keeps it that way.

Where the "after initialize" signal comes from
----------------------------------------------
:class:`~trw_mcp.middleware.boot_deferral.BootDeferralMiddleware`. FastMCP routes
the ``initialize`` request through the middleware chain and ``call_next`` is what
responds, so everything after that ``await`` provably postdates the reply — not a
sleep, not a best-effort race, and no private attribute.

A client that never gets that far, a budget overrun, or an error all leave the
resolution incomplete. That is why the first tool call runs
:func:`ensure_deferred_boot_work` inline: NFR02 makes this half fail-CLOSED
toward correctness, because a tool must never observe an unresolved sync
configuration. It pays the latency instead.

Budget
------
``boot_deferred_work_budget_ms`` bounds the scheduled attempt. Exceeding it
emits ``boot_deferred_work_budget_exceeded`` and abandons the attempt; the state
stays "not done", so the first tool call re-runs it.
"""

from __future__ import annotations

import asyncio
import threading

import structlog

from trw_mcp.server._boot_timeline import emit_boot_phase

logger = structlog.get_logger(__name__)

__all__ = [
    "cancel_sync_task",
    "deferred_work_done",
    "deferred_work_in_flight",
    "ensure_deferred_boot_work",
    "remember_serving_loop",
    "reset_deferred_boot_state",
    "schedule_deferred_boot_work",
]

_lock = threading.Lock()
#: True only once a resolution has FINISHED SUCCESSFULLY. Never set on claim.
_completed = False
#: True while an attempt is executing, on whatever thread claimed it.
_running = False
#: Signalled when the current attempt ends, success or failure. A waiter blocks
#: on this rather than on ``_completed``, because the state that must gate a
#: tool call is "an attempt is in flight", not "an attempt was claimed".
_attempt_finished = threading.Event()
_sync_task: asyncio.Task[None] | None = None

#: How long a waiting tool call stays silent before it starts reporting that it
#: is still blocked. It keeps waiting afterwards — see
#: :func:`ensure_deferred_boot_work` for why the wait is not bounded — but an
#: operator gets a periodic event rather than an unexplained stall.
_WAIT_REPORT_INTERVAL_SECONDS = 5.0


def deferred_work_done() -> bool:
    """Whether the resolution has COMPLETED.

    Deliberately not "has been claimed". An in-flight attempt answers ``False``
    here, so a caller checking this cannot mistake a claim for a result.
    """
    with _lock:
        return _completed


def deferred_work_in_flight() -> bool:
    """Whether an attempt is executing right now (on any thread)."""
    with _lock:
        return _running


def reset_deferred_boot_state() -> None:
    """Clear both latches and the owned task handle (tests, re-boot)."""
    global _completed, _running, _sync_task
    with _lock:
        _completed = False
        _running = False
        _sync_task = None
    _attempt_finished.set()


def ensure_deferred_boot_work() -> bool:
    """Guarantee the resolution has completed before returning. Fail-CLOSED.

    Returns True iff this call performed the resolution.

    Three states, and the middle one is the whole point of the function:

    * **completed** — return immediately.
    * **in flight** — WAIT for the running attempt. This is the case the first
      implementation got wrong: it set the completion latch on *claim*, so a
      tool call arriving while resolution was still executing (very much
      including after a budget timeout, since ``asyncio.wait_for`` cancels the
      await and not the worker thread) saw "done" and proceeded against an
      unresolved sync configuration — the exact thing NFR02 says must not
      happen.
    * **idle** — claim it and run it here.

    The wait is not bounded. NFR02 makes this half fail-CLOSED toward
    correctness: a tool must never observe an unresolved sync configuration, so
    the call pays the latency instead. ``_resolve_backend_sync`` performs no
    network I/O (it looks up URLs/keys and creates a task), so it terminates;
    the budget exists to catch a future contributor adding I/O, and a blocked
    tool call reporting itself every few seconds is how that would surface.
    """
    global _completed, _running
    while True:
        with _lock:
            if _completed:
                return False
            if not _running:
                _running = True
                _attempt_finished.clear()
                break
        _await_in_flight_attempt()

    try:
        _resolve_backend_sync()
    except Exception:  # justified: fail-open on the ERROR path; the latch stays clear so we retry
        logger.warning("boot_deferred_work_failed", exc_info=True)
        with _lock:
            _running = False
        _attempt_finished.set()
        return False
    with _lock:
        _running = False
        _completed = True
    _attempt_finished.set()
    emit_boot_phase("deferred_work_complete")
    return True


def _await_in_flight_attempt() -> None:
    """Block until the in-flight attempt ends, reporting periodically."""
    waited = 0.0
    while not _attempt_finished.wait(_WAIT_REPORT_INTERVAL_SECONDS):
        waited += _WAIT_REPORT_INTERVAL_SECONDS
        logger.warning(
            "boot_deferred_work_wait",
            waited_seconds=waited,
            detail="a tool call is blocked on the in-flight backend-sync resolution (fail-closed)",
        )


async def schedule_deferred_boot_work(budget_ms: int) -> None:
    """Run the deferred step off the handshake under *budget_ms*; fail-open.

    The budget bounds how long this AWAIT lasts, not the worker: ``wait_for``
    cancels the future, and the thread it wrapped keeps running to completion.
    That is fine, and it is why the completion latch is separate from the claim
    — a tool call arriving after the timeout waits for the still-running
    attempt instead of proceeding past it.
    """
    if deferred_work_done():
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(ensure_deferred_boot_work), timeout=budget_ms / 1000)
    except TimeoutError:
        logger.warning(
            "boot_deferred_work_budget_exceeded",
            budget_ms=budget_ms,
            action="the attempt keeps running; the first tool call blocks on it rather than proceeding",
        )
    except Exception:  # justified: fail-open, the deferred step must never take the server down
        logger.warning("boot_deferred_work_failed", exc_info=True)


def cancel_sync_task() -> asyncio.Task[None] | None:
    """Return and clear the owned sync task so the lifespan can cancel it."""
    global _sync_task
    with _lock:
        task, _sync_task = _sync_task, None
    if task is not None:
        task.cancel()
    return task


def _resolve_backend_sync() -> None:
    """Resolve backend-sync config/targets/profile and start the sync loop.

    This is verbatim the work the lifespan used to do before ``yield`` — only
    its position in time changed. The sync loop task is created on the running
    loop when there is one; ``_app``'s lifespan owns cancelling it at shutdown.
    """
    global _sync_task
    from trw_mcp.server._app import _try_load_config

    config = _try_load_config()
    if config is None:
        logger.info("sync_config_resolved", source="none")
        return
    backend_url = config.resolved_backend_url
    backend_api_key = config.resolved_backend_api_key
    if not (backend_url and backend_api_key):
        logger.info("sync_config_resolved", source="none")
        return

    if config.backend_url and config.backend_api_key:
        source = "explicit"
    elif config.backend_url or config.backend_api_key:
        source = "mixed"
    else:
        source = "platform_fallback"
    # PRD-SEC-004-FR05/FR01: the sync client still starts (pull/intel is
    # unaffected and credential resolution must keep working), but CONTENT
    # egress is consent-gated downstream in BackendSyncClient._run_one_cycle.
    # Surface the resolved consent state here so an operator can verify opt-out.
    logger.info(
        "sync_config_resolved",
        source=source,
        url=backend_url,
        learning_sharing_enabled=bool(getattr(config, "learning_sharing_enabled", False)),
        platform_telemetry_enabled=bool(getattr(config, "platform_telemetry_enabled", False)),
    )

    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.sync.client import BackendSyncClient

    _start_sync_loop(BackendSyncClient(config=config, trw_dir=resolve_trw_dir()))


def _start_sync_loop(sync_client: object) -> None:
    """Create the sync-loop task ON the serving event loop, from any thread.

    Never blocks on the loop: the deferred step runs on a worker thread when it
    was scheduled and on the loop thread itself when a tool call ran it inline,
    and a ``run_coroutine_threadsafe(...).result()`` would deadlock in the second
    case. ``call_soon_threadsafe`` is correct for both.
    """
    loop = _serving_loop()
    if loop is None:
        logger.info("sync_loop_not_started", reason="no serving event loop available")
        return

    def _create() -> None:
        global _sync_task
        with _lock:
            _sync_task = asyncio.create_task(sync_client.run_sync_loop())  # type: ignore[attr-defined]

    if _current_loop() is loop:
        _create()
    else:
        loop.call_soon_threadsafe(_create)


_loop: asyncio.AbstractEventLoop | None = None


def remember_serving_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the serving event loop so worker-thread resolution can reach it.

    Called from the lifespan, which runs on that loop.
    """
    global _loop
    _loop = loop


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _serving_loop() -> asyncio.AbstractEventLoop | None:
    running = _current_loop()
    if running is not None:
        return running
    return _loop if _loop is not None and not _loop.is_closed() else None
