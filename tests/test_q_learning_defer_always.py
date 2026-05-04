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
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _invoke_build_check(tmp_project: Path) -> dict[str, object]:
    """Invoke trw_build_check via the FastMCP server fixture."""
    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("build")
    fn = extract_tool_fn(server, "trw_build_check")

    import trw_mcp.tools.build._registration as reg_mod

    original_resolve = reg_mod.resolve_trw_dir
    reg_mod.resolve_trw_dir = lambda: tmp_project / ".trw"  # type: ignore[assignment]
    try:
        return fn(tests_passed=True, test_count=1, scope="full")  # type: ignore[no-any-return]
    finally:
        reg_mod.resolve_trw_dir = original_resolve  # type: ignore[assignment]


def test_response_carries_q_learning_deferred_field(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01 acceptance: ``q_learning_deferred`` is ALWAYS present, with reason='deferred_always'."""
    # Patch process_outcome_for_event so the bg thread does no real work.
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )
    result = _invoke_build_check(tmp_project)

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


def test_q_learning_runs_in_background_not_inline(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: ``process_outcome_for_event`` MUST NOT have been called when the tool returns.

    Block the worker on a threading.Event, fire the tool, assert the
    event is still UNSET when the response comes back. This proves
    Q-learning is on the bg thread and not inline.
    """
    started = threading.Event()
    proceed = threading.Event()
    completed = threading.Event()

    def slow_correlation(event_type: str, event_data: object = None) -> list[str]:
        started.set()
        proceed.wait(timeout=5.0)
        completed.set()
        return []

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", slow_correlation)

    t0 = time.monotonic()
    result = _invoke_build_check(tmp_project)
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
    assert result["q_learning_deferred"]["thread_state"] == "launched"  # type: ignore[index]

    # Unblock the worker so the autouse fixture can join it cleanly.
    proceed.set()
    assert completed.wait(timeout=5.0)


def test_concurrent_calls_coalesce_via_queue(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01 reentrancy: a second call while the worker is alive enqueues, doesn't spawn a peer thread."""
    started = threading.Event()
    proceed = threading.Event()
    call_count = MagicMock()

    def gated_correlation(event_type: str, event_data: object = None) -> list[str]:
        call_count(event_type)
        started.set()
        proceed.wait(timeout=5.0)
        return []

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", gated_correlation)

    # First call: launches the worker (which blocks on `proceed`).
    first = _invoke_build_check(tmp_project)
    assert started.wait(timeout=2.0)
    assert first["q_learning_deferred"]["thread_state"] == "launched"  # type: ignore[index]

    # Second call: worker is alive, so this MUST enqueue.
    second = _invoke_build_check(tmp_project)
    assert second["q_learning_deferred"]["thread_state"] == "queued", (
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

    assert call_count.call_count == 2, (
        f"worker should have processed initial + queued event (2 calls), got "
        f"{call_count.call_count}. Coalescing-queue drain is broken."
    )


def test_worker_crash_clears_thread_handle(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01 crash recovery: when the worker raises, _q_thread is cleared so the next call retries."""

    def raising_correlation(event_type: str, event_data: object = None) -> list[str]:
        raise RuntimeError("simulated worker crash")

    monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", raising_correlation)

    _invoke_build_check(tmp_project)

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

    # Sanity: error count is bumped, last_error captures the exception class.
    from trw_mcp.tools.build._registration import get_q_learning_health

    health = get_q_learning_health()
    assert health["error_count"] >= 1
    assert "RuntimeError" in str(health["last_error"])
