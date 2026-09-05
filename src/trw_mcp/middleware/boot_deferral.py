"""Post-``initialize`` scheduling and the first-tool-call fallback (PRD-CORE-248 FR01/FR02).

Two hooks, one invariant: backend-sync resolution happens **after** the
``initialize`` result is emitted, and **before** any tool body runs.

``on_initialize``
    FastMCP routes the ``initialize`` request through the middleware chain
    (``fastmcp.server.low_level.MiddlewareServerSession._received_request``), and
    ``call_next`` is what responds. Everything after the ``await`` therefore
    provably postdates the reply — no sleep, no race, no private attribute. That
    is where the ``boot_phase=initialize_answered`` event is emitted and the
    deferred step is scheduled.

``on_call_tool``
    The fallback. A client that skips ``notifications/initialized``, a budget
    overrun, or an error all leave the resolution incomplete, and NFR02 makes
    this half fail-CLOSED toward correctness: no tool call may observe an
    unresolved sync configuration, so the call runs the resolution inline and
    pays the latency. Once resolved this is one boolean read per call.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastmcp.server.middleware import Middleware, MiddlewareContext

from trw_mcp.server._boot_timeline import emit_boot_phase

logger = structlog.get_logger(__name__)

__all__ = ["BootDeferralMiddleware"]


class BootDeferralMiddleware(Middleware):
    """Schedule the deferred boot step after ``initialize``; run it inline if needed.

    Args:
        budget_ms: ``boot_deferred_work_budget_ms``. Resolved once at
            construction so the hot path never touches config.
    """

    def __init__(self, budget_ms: int) -> None:
        self.budget_ms = budget_ms
        #: Retained so the scheduled task is not garbage-collected mid-flight.
        self._task: asyncio.Task[None] | None = None

    async def on_initialize(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        """Emit the timeline event and schedule the deferred step AFTER the reply."""
        result = await call_next(context)
        try:
            emit_boot_phase("initialize_answered")
            from trw_mcp.server._boot_deferred import schedule_deferred_boot_work

            self._task = asyncio.create_task(schedule_deferred_boot_work(self.budget_ms))
        except Exception:  # justified: fail-open, boot bookkeeping must never fail a handshake
            logger.warning("boot_deferral_schedule_failed", exc_info=True)
        return result

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        """Block until backend sync is resolved, then let the tool run.

        ``ensure_deferred_boot_work`` returns only once the resolution has
        COMPLETED — it runs the work when nothing has claimed it, and waits when
        an attempt is already in flight (including one whose scheduling budget
        already expired, since the timeout cancels the await and not the worker
        thread). That is the fail-closed half of NFR02: a tool call pays the
        latency rather than proceeding against an unresolved configuration.

        It runs on a worker thread, not here: a blocking wait on the event-loop
        thread would stall the very loop the scheduled attempt needs in order to
        finish creating its sync task.
        """
        try:
            from trw_mcp.server._boot_deferred import deferred_work_done, ensure_deferred_boot_work

            if not deferred_work_done():
                ran_here = await asyncio.to_thread(ensure_deferred_boot_work)
                # ``ensure_deferred_boot_work`` answers False for two opposite
                # outcomes: someone else's attempt completed, or the attempt
                # RAISED and the completion latch is still clear. Logging both
                # as "awaited" hid the second — the one an operator needs,
                # because the tool call is about to proceed fail-open against
                # an unresolved sync configuration. The completion latch is the
                # authoritative discriminator, so read it rather than infer it.
                if ran_here:
                    logger.info(
                        "boot_deferred_work_ran_inline",
                        reason="the first tool call arrived before the scheduled step completed",
                    )
                elif deferred_work_done():
                    logger.info(
                        "boot_deferred_work_awaited",
                        reason="another caller's attempt completed while this tool call waited",
                    )
                else:
                    logger.warning(
                        "boot_deferred_work_failed_inline",
                        reason="the resolution attempt ended without completing; this tool call "
                        "proceeds against an unresolved sync configuration (NFR02 fail-open)",
                    )
        except Exception:  # justified: fail-open, a boot fallback must never fail a tool call
            logger.warning("boot_deferral_middleware_failed", exc_info=True)
        return await call_next(context)
