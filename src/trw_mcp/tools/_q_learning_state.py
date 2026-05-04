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

# --- Single-flight worker handle ---
# Held by the launcher; the worker clears it in a ``finally`` block so a
# crash leaves no zombie reference.
_q_thread: threading.Thread | None = None

# Lock around ``_q_thread`` and ``_q_queue`` mutation.
_q_lock = threading.Lock()

# Bounded coalescing queue. Worker drains it before exiting; launcher
# enqueues when a worker is already alive.
_q_queue: queue.Queue[str] = queue.Queue(maxsize=16)
