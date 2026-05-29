"""Background-thread state for Q-learning outcome correlation (PRD-FIX-088 FR01).

Owns the single-flight worker handle, the coalescing event queue, and the
shutdown-join helper for ``trw_build_check``'s deferred Q-learning path.

Why a separate module from ``_deferred_state``:
    ``_deferred_state._deferred_thread`` is owned by ``trw_deliver`` and runs
    a long-lived batch (consolidation, telemetry, publish, etc.) that can take
    tens of seconds. ``trw_build_check`` fires far more often and has tighter
    latency expectations. Sharing the same thread handle would cause
    deliver-batches to block build-check Q-learning (and vice versa). Keeping
    them separate is single-responsibility per worker pool.

Single-flight + coalescing-queue contract:
    - At most one Q-learning worker thread alive at any time.
    - WHEN a build_check fires while a worker is alive, the new event is
      enqueued on a bounded ``queue.Queue(maxsize=16)`` that the running
      worker drains AFTER finishing the current pass, then exits.
    - WHEN the queue is full, the new event is dropped with a WARNING log.
      Q-learning's source of truth is ``recall_tracking.jsonl``; the next
      pass re-scans it, so dropping a single event is recoverable.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

# --- Single-flight worker handle ---
# Held by the launcher; the worker clears it in a ``finally`` block so a
# crash leaves no zombie reference.
_q_thread: threading.Thread | None = None

# Lock around ``_q_thread`` and ``_q_queue`` mutation.
_q_lock = threading.Lock()

# Bounded coalescing queue. Worker drains it before exiting; launcher
# enqueues when a worker is already alive.
# PRD-FIX-088: queued items carry the originating ``tool_call_id`` so
# async correlation log events can be threaded back to the call that
# enqueued them. Tuple shape: ``(event_type, tool_call_id)``.
_q_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=16)


# --- Worker health (PRD-FIX-088 P1.5 Fix 9, round-2 thread-safety) ---
# Replaces module-level scalar globals with a dataclass so conftest can
# zero it out in one assignment and ``get_q_learning_health`` reads a
# single object instead of two ``global`` declarations.
#
# Thread-safety (round-2 finding F2): ``error_count``/``last_error`` are
# written by the bg worker and read by the main thread. Mutations must
# go through ``record_error()`` / ``mark_success()`` which take the
# shared ``_q_lock``; reads use ``snapshot()`` so the pair is consistent
# (no torn read of newer count + stale message).
@dataclass
class _QLearningHealth:
    """Aggregated background-worker health counters."""

    error_count: int = 0
    last_error: str | None = None


_health: _QLearningHealth = _QLearningHealth()


def record_error(exc: BaseException) -> int:
    """Atomically bump ``error_count`` and stamp ``last_error``.

    Returns the new ``error_count`` for use in log payloads. Holding
    ``_q_lock`` ensures concurrent readers see a consistent
    ``(count, message)`` pair.
    """
    with _q_lock:
        _health.error_count += 1
        _health.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        return _health.error_count


def mark_success() -> None:
    """Clear ``last_error`` after a successful correlation pass."""
    with _q_lock:
        _health.last_error = None


def snapshot() -> tuple[int, str | None]:
    """Return ``(error_count, last_error)`` as a coherent pair."""
    with _q_lock:
        return _health.error_count, _health.last_error


def reset_health() -> None:
    """Test-helper: zero the worker-health counters between tests."""
    with _q_lock:
        _health.error_count = 0
        _health.last_error = None


def join_q_learning_worker(timeout: float = 30.0) -> None:
    """Join the background Q-learning worker thread, if alive.

    PRD-FIX-088 FR01 "Shutdown + recovery contract": ``trw_deliver`` SHALL
    join the worker with ``timeout=30.0`` so last-pass durability is
    guaranteed before process exit. The worker is daemon=True so process
    exit doesn't strictly require it, but the PRD mandates it.
    """
    with _q_lock:
        t = _q_thread
    if t is not None and t.is_alive():
        t.join(timeout=timeout)
