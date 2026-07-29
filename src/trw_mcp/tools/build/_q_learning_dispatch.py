"""Deferred Q-learning dispatch for ``trw_build_check`` (PRD-FIX-088 FR01).

Belongs to the ``build/_registration.py`` facade. Re-exported there for
back-compat.

Pre-fix, outcome correlation ran inline and could take >90 s on large corpora,
holding the MCP response on the SSE stream for the entire duration. It is now
ALWAYS deferred to a single-flight background worker with a bounded coalescing
queue. The worker handle, lock, and health counters live in
``tools/_q_learning_state``; this module owns the launch/drain policy.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from queue import Empty
from typing import Literal

import structlog

import trw_mcp.tools._q_learning_state as _qls
from trw_mcp.models.typed_dicts._tools import (
    QLearningDeferredDict,
    QLearningHealthDict,
)

logger = structlog.get_logger(__name__)

# Literal alias for the ``thread_state`` field on :class:`QLearningDeferredDict`.
# Kept private since it is an implementation detail of the dispatcher.
_DispatchThreadState = Literal["launched", "queued", "queue_full"]


def _dispatch_q_learning_async(
    event_type: str,
    scope: str,
    tool_call_id: str,
) -> QLearningDeferredDict:
    """Schedule Q-learning outcome correlation on the background worker.

    Returns a stable :class:`QLearningDeferredDict` (always non-None) with
    a literal ``reason`` and a literal ``thread_state`` so log readers and
    tests get static-typed access without ``cast`` / ``# type: ignore``.

    PRD-FIX-088 FR01: ``tool_call_id`` is threaded through so the async
    ``q_learning_complete`` and ``outcome_correlation_applied`` events the
    worker emits can be correlated back to the originating tool call.
    """
    scheduled_at = datetime.now(timezone.utc).isoformat()
    thread_state: _DispatchThreadState
    with _qls._q_lock:
        worker_alive = _qls._q_thread is not None and _qls._q_thread.is_alive()
        if worker_alive:
            try:
                _qls._q_queue.put_nowait((event_type, tool_call_id))
                thread_state = "queued"
            except Exception:  # justified: bounded queue overflow is best-effort
                logger.warning(
                    "q_learning_queue_full",
                    event_type=event_type,
                    scope=scope,
                    queue_max=_qls._q_queue.maxsize,
                    tool_call_id=tool_call_id,
                )
                thread_state = "queue_full"
        else:
            _qls._q_thread = threading.Thread(
                target=_q_learning_worker,
                args=(event_type, scope, tool_call_id),
                name="trw-q-learning",
                daemon=True,
            )
            _qls._q_thread.start()
            thread_state = "launched"
    return QLearningDeferredDict(
        reason="deferred_always",
        scheduled_at=scheduled_at,
        thread_state=thread_state,
        tool_call_id=tool_call_id,
    )


def _q_learning_worker(
    initial_event_type: str,
    scope: str,
    tool_call_id: str,
) -> None:
    """Background worker: process the initial event then drain the coalescing queue.

    Single-flight contract: only one worker at a time. While alive, peer
    callers enqueue ``(event_type, tool_call_id)`` onto ``_q_queue``;
    after the initial pass completes, the worker drains the queue and
    exits. The handle is cleared in ``finally`` so a crash leaves no
    zombie reference.

    PRD-FIX-088 P1.5 Fix 6: catches ``Exception`` (not ``BaseException``)
    so daemon threads do not swallow ``KeyboardInterrupt``/``SystemExit``.
    P1.5 Fix 8: this is the **single** crash-recording site — the inner
    helper now raises straight through so ``q_learning_worker_crashed``
    is the one accurate event when correlation throws.
    """
    try:
        _process_q_learning_inline(initial_event_type, scope, tool_call_id)
        # Drain coalescing queue until empty. ``get_nowait`` returns
        # immediately on empty, breaking the loop.
        while True:
            try:
                queued_event, queued_call_id = _qls._q_queue.get_nowait()
            except Empty:
                break
            _process_q_learning_inline(queued_event, scope, queued_call_id)
    except Exception as exc:  # justified: bg-thread last-resort barrier
        # PRD-FIX-088 round-2 F2: atomic count + last_error update via
        # ``_q_lock``-guarded helper; prevents torn reads from
        # ``get_q_learning_health()``.
        new_count = _qls.record_error(exc)
        logger.exception(
            "q_learning_worker_crashed",
            event_type=initial_event_type,
            scope=scope,
            error_count=new_count,
            tool_call_id=tool_call_id,
        )
    finally:
        with _qls._q_lock:
            _qls._q_thread = None


def _process_q_learning_inline(
    event_type: str,
    scope: str,
    tool_call_id: str,
) -> None:
    """Run a single Q-learning correlation pass and record outcome.

    PRD-FIX-088 P1.5 Fix 8: previously this caught ``Exception`` and
    logged ``q_learning_failed``, which made the worker's outer
    ``except`` unreachable for normal failures and produced two
    overlapping error events. The catch has been removed; exceptions
    propagate to the worker and are recorded once via
    ``q_learning_worker_crashed``.

    Note (Fix 10): the import of ``process_outcome_for_event`` is
    deferred here to avoid a potential ``trw_mcp.scoring`` <-> ``tools``
    import cycle at module-load time. The function is called once per
    pass, so the per-call import cost is negligible.
    """
    from trw_mcp.scoring import process_outcome_for_event

    updated = process_outcome_for_event(
        event_type,
        tool_call_id=tool_call_id,
    )
    logger.info(
        "q_learning_complete",
        event_type=event_type,
        scope=scope,
        updated_count=len(updated),
        tool_call_id=tool_call_id,
    )
    # PRD-FIX-088 round-2 F2: lock-guarded clear via helper.
    _qls.mark_success()


def get_q_learning_health() -> QLearningHealthDict:
    """Return Q-learning worker health for observability.

    Round-2 F2: ``snapshot()`` returns ``(count, last_error)`` as a
    coherent pair under ``_q_lock`` so callers never see a newer count
    paired with a stale message.
    """
    worker_alive = _qls._q_thread is not None and _qls._q_thread.is_alive()
    error_count, last_error = _qls.snapshot()
    return QLearningHealthDict(
        queue_size=_qls._q_queue.qsize(),
        error_count=error_count,
        last_error=last_error,
        worker_alive=worker_alive,
    )
