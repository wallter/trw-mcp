"""Shared state for deferred delivery -- thread handle, lock, and watchdog signals.

Extracted to break the circular import between ceremony.py and
_deferred_delivery.py. Both modules import from here instead of
from each other.

Watchdog integration
--------------------
``_cancel_event`` is set by the watchdog (or by a graceful shutdown) to
ask any in-flight deferred step to stop at the next opportunity. Steps
poll the event between expensive iterations; ``auto_prune`` checks it
between batches of dedup comparisons. Setting the event is advisory —
steps that don't poll will continue until completion or until the
watchdog tears the thread down.

``_last_auto_prune_at`` is the process-local monotonic timestamp of the
most recent successful ``auto_prune`` run. The throttle in
``_step_auto_prune`` consults it to skip runs that fall inside the
``learning_auto_prune_min_interval_hours`` window. The state is
process-local on purpose: surviving a process restart is fine because
the cost of an occasional extra prune is small compared with the
complexity of a persistent file-based marker.
"""

from __future__ import annotations

import threading

# Deferred delivery thread handle and lock.
# Previously lived in ceremony.py; extracted here so
# _deferred_delivery.py can import without a cycle.
_deferred_thread: threading.Thread | None = None
_deferred_lock = threading.Lock()

# Cooperative cancellation signal flipped by the watchdog when a deferred
# step exceeds its per-step or per-batch budget. Steps that perform long
# loops (auto_prune, consolidation) poll this between iterations and
# return early when it is set. Reset on every new batch launch.
_cancel_event: threading.Event = threading.Event()

# Process-local timestamp (time.monotonic()) of the most recent successful
# auto_prune pass. ``_step_auto_prune`` consults this against the
# ``learning_auto_prune_min_interval_hours`` config to throttle runs.
_last_auto_prune_at: float | None = None
