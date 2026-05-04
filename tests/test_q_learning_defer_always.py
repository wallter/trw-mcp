"""PRD-FIX-088 FR01: Q-learning is ALWAYS deferred to a background worker.

Pre-fix: ``trw_build_check`` ran ``process_outcome_for_event`` inline unless
writer pressure was detected. With a 60-min correlation window and ~5K
recall receipts/hour, a single call could correlate >2800 entries and
take 91 seconds, holding the MCP response on the SSE stream the whole time.

Post-fix: every ``trw_build_check`` schedules Q-learning on
``_q_learning_state._q_thread``. The response always carries
``q_learning_deferred`` with a stable shape. Concurrent calls coalesce
onto a bounded queue (single-flight worker).

These tests are the regression guard. If a future change reverts to
inline Q-learning (or drops the ``q_learning_deferred`` field), the
assertions below fail loudly.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest


def test_response_carries_q_learning_deferred_field(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR01 acceptance: ``q_learning_deferred`` is ALWAYS present, with reason='deferred_always'."""
    # Patch process_outcome_for_event so the bg thread does no real work.
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None, **_kw: [],
    )
    result = build_check_invoke()

    assert "q_learning_deferred" in result, (
        "FR01: response MUST always include q_learning_deferred. Pre-fix this "
        "field was only set under writer pressure."
    )
    deferred = result["q_learning_deferred"]
    assert isinstance(deferred, dict)
    assert deferred["reason"] == "deferred_always"
    assert isinstance(deferred["scheduled_at"], str)
    assert deferred["thread_state"] in {"launched", "queued"}, (
        f"thread_state must be one of launched/queued/queue_full, got {deferred['thread_state']!r}"
    )
    # PRD-FIX-088 P1 Fix 2: tool_call_id MUST appear on the deferred dict so
    # log readers can correlate the async completion to the originating call.
    assert isinstance(deferred["tool_call_id"], str)
    assert deferred["tool_call_id"], "tool_call_id must be a non-empty string"


def test_q_learning_runs_in_background_not_inline(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR01: ``process_outcome_for_event`` MUST NOT have been called when the tool returns.

    Block the worker on a threading.Event, fire the tool, assert the
    event is still UNSET when the response comes back. This proves
    Q-learning is on the bg thread and not inline.
    """
    started = threading.Event()
    proceed = threading.Event()
    completed = threading.Event()

    def slow_correlation(event_type: str, event_data: object = None, **_kw: object) -> list[str]:
        started.set()
        proceed.wait(timeout=5.0)
        completed.set()
        return []

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", slow_correlation)

    t0 = time.monotonic()
    result = build_check_invoke()
    elapsed = time.monotonic() - t0

    # The worker should have started (background thread launched and ran
    # ``process_outcome_for_event`` which set ``started``), but NOT yet
    # completed because we haven't unblocked it.
    assert started.wait(timeout=2.0), "bg worker must have started by now"
    assert not completed.is_set(), (
        "FR01 regression: q_learning ran inline. Worker completed before "
        "trw_build_check returned, meaning the bg thread didn't actually "
        "defer the work."
    )
    # Tool itself returned quickly because it dispatches and returns.
    assert elapsed < 1.0, f"trw_build_check took {elapsed:.2f}s — should be near-instant"
    deferred = result["q_learning_deferred"]
    assert isinstance(deferred, dict)
    assert deferred["thread_state"] == "launched"

    # Unblock the worker so the autouse fixture can join it cleanly.
    proceed.set()
    assert completed.wait(timeout=5.0)


def test_concurrent_calls_coalesce_via_queue(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR01 reentrancy: a second call while the worker is alive enqueues, doesn't spawn a peer thread."""
    started = threading.Event()
    proceed = threading.Event()
    # PRD-FIX-088 P1.5 Fix 12: PRD test design specified ``list[str]``
    # rather than a MagicMock — clearer signal, no implicit equality
    # surprises, and this captures the event_type ordering for asserts.
    events: list[str] = []
    events_lock = threading.Lock()

    def gated_correlation(event_type: str, event_data: object = None, **_kw: object) -> list[str]:
        with events_lock:
            events.append(event_type)
        started.set()
        proceed.wait(timeout=5.0)
        return []

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", gated_correlation)

    # First call: launches the worker (which blocks on `proceed`).
    first = build_check_invoke()
    assert started.wait(timeout=2.0)
    deferred_first = first["q_learning_deferred"]
    assert isinstance(deferred_first, dict)
    assert deferred_first["thread_state"] == "launched"

    # Second call: worker is alive, so this MUST enqueue.
    second = build_check_invoke()
    deferred_second = second["q_learning_deferred"]
    assert isinstance(deferred_second, dict)
    assert deferred_second["thread_state"] == "queued", (
        "FR01: while bg worker is alive, peer call must enqueue, not spawn "
        "a second thread."
    )

    # Unblock the worker; it should drain the queued event and exit.
    proceed.set()

    # Verify worker drains the queue: process_outcome_for_event called twice
    # (once for the initial pass, once for the queued event).
    import trw_mcp.tools._q_learning_state as _qls

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with _qls._q_lock:
            t = _qls._q_thread
        if t is None or not t.is_alive():
            break
        time.sleep(0.05)

    with events_lock:
        assert len(events) == 2, (
            f"worker should have processed initial + queued event (2 calls), got "
            f"{len(events)}: {events!r}. Coalescing-queue drain is broken."
        )


def test_worker_crash_clears_thread_handle(
    build_check_invoke: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR01 crash recovery: when the worker raises, _q_thread is cleared so the next call retries."""
    import structlog.testing

    def raising_correlation(event_type: str, event_data: object = None, **_kw: object) -> list[str]:
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", raising_correlation)

    # PRD-FIX-088 P1.5 Fix 8: assert ``q_learning_worker_crashed`` is the
    # single accurate event when the worker raises. Pre-fix the inner
    # ``try/except`` in ``_process_q_learning_inline`` shadowed the worker's
    # outer handler, so this regression catch was untested.
    with structlog.testing.capture_logs() as logs:
        build_check_invoke()

        import trw_mcp.tools._q_learning_state as _qls

        # Wait for worker to finish (with the simulated crash).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with _qls._q_lock:
                t = _qls._q_thread
            if t is None or not t.is_alive():
                break
            time.sleep(0.05)

    with _qls._q_lock:
        assert _qls._q_thread is None, (
            "FR01 crash contract: worker crash must clear _q_thread in finally so "
            "the next trw_build_check call can launch a fresh worker."
        )

    # Worker's outer ``except`` (now ``Exception``, not ``BaseException``)
    # must have logged exactly one ``q_learning_worker_crashed``.
    crash_events = [e for e in logs if e.get("event") == "q_learning_worker_crashed"]
    assert crash_events, (
        "PRD-FIX-088 Fix 8: worker MUST emit q_learning_worker_crashed on raise. "
        "Pre-fix the inner inline handler swallowed exceptions and only logged "
        "q_learning_failed, which is unreachable for the crash path."
    )

    # Sanity: error count is bumped, last_error captures the exception class.
    from trw_mcp.tools.build._registration import get_q_learning_health

    health = get_q_learning_health()
    assert health["error_count"] >= 1
    assert "RuntimeError" in str(health["last_error"])
