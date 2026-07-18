"""TelemetryPipeline self-start / atexit-drain tests (F23 process-break fix).

Before this fix, ``TelemetryPipeline.start()`` ran ONLY from the
``trw_session_start`` path (``tools/_ceremony_telemetry.py``). Events handed
to ``enqueue()`` before that call -- or in any process where session_start
never runs -- queued safely in the deque but the background flush thread
never started, so they never reached the sender boundary.

These tests drive the REAL pipeline: ``enqueue()`` with NO prior explicit
``start()`` must (a) auto-start the flush thread so the event reaches the
sender, and (b) keep that start idempotent (one thread, not N). A separate
test proves the ``atexit`` drain path flushes queued events on shutdown.

Only the network boundary is mocked: ``_send_batch`` is patched to a
capturing stub. ``enqueue``/``start``/``_timer_loop``/``flush_now`` all run
for real.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests._telemetry_pipeline_support import (
    _make_event,
    _make_fake_cfg,
    make_configured_pipeline,
)

from ._telemetry_pipeline_support import pipeline_cls  # noqa: F401


def _capture_sender(pipeline: Any) -> list[dict[str, object]]:
    """Patch the pipeline's network boundary to capture sent events.

    Returns the live list that ``_send_batch`` appends every event into.
    Returning True keeps ``flush_now``'s "all sent" path so the local JSONL
    is truncated exactly as in production.
    """
    captured: list[dict[str, object]] = []

    def fake_send(events: list[dict[str, object]], urls: list[str], api_key: str) -> bool:
        captured.extend(events)
        return True

    pipeline._send_batch = fake_send  # type: ignore[method-assign]
    return captured


def _build_pipeline(
    pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, list[dict[str, object]]]:
    """Construct a fast-flush pipeline with a remote URL and captured sender."""
    pipeline, _ = make_configured_pipeline(
        pipeline_cls,
        tmp_path,
        monkeypatch,
        cfg=_make_fake_cfg(effective_platform_urls=["https://example.test"]),
        pipeline_kwargs={
            "flush_interval_secs": 0.05,
            "batch_size": 100,
            "max_retries": 1,
            "backoff_base": 0.0,
        },
    )
    captured = _capture_sender(pipeline)
    return pipeline, captured


def _wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    """Spin until predicate() is truthy or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class TestEnqueueAutoStartsFlushThread:
    """enqueue() without a prior start() still delivers events."""

    def test_enqueue_without_start_auto_starts_thread(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare enqueue() (no start()) starts the flush thread."""
        pipeline, _ = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        try:
            assert pipeline._thread is None, "precondition: no thread before enqueue"

            pipeline.enqueue(_make_event(tool_name="trw_learn"))

            assert _wait_for(lambda: pipeline._thread is not None and pipeline._thread.is_alive()), (
                "enqueue() must auto-start a live flush thread"
            )
        finally:
            pipeline.stop(drain=False, timeout=5.0)

    def test_enqueue_without_start_event_reaches_sender(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The auto-started timer flushes the queued event to the sender."""
        pipeline, captured = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        try:
            pipeline.enqueue(_make_event(tool_name="trw_deliver", duration_ms=7))

            assert _wait_for(lambda: len(captured) >= 1), (
                "queued event must reach the sender boundary without an explicit start()"
            )
            tool_names = {ev.get("tool_name") for ev in captured}
            assert "trw_deliver" in tool_names, "the exact event must be delivered"
        finally:
            pipeline.stop(drain=False, timeout=5.0)


class TestAutoStartIdempotency:
    """Repeated enqueues never spawn more than one flush thread."""

    def test_many_enqueues_spawn_single_thread(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sequential enqueues reuse the same thread object."""
        pipeline, _ = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        try:
            pipeline.enqueue(_make_event(seq=0))
            assert _wait_for(lambda: pipeline._thread is not None and pipeline._thread.is_alive())
            first_thread = pipeline._thread

            for i in range(1, 25):
                pipeline.enqueue(_make_event(seq=i))

            assert pipeline._thread is first_thread, "repeated enqueues must not replace the flush thread"
            assert pipeline._thread.is_alive()
        finally:
            pipeline.stop(drain=False, timeout=5.0)

    def test_concurrent_first_enqueues_spawn_single_thread(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Many threads racing on the first enqueue create exactly one thread.

        Guards the race the lazy start introduces: without the lock around the
        alive-check + thread creation, two first-time callers could each spawn
        a flush thread.
        """
        pipeline, _ = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        seen_threads: list[threading.Thread] = []
        seen_lock = threading.Lock()
        n_threads = 24
        barrier = threading.Barrier(n_threads)

        def worker(tid: int) -> None:
            barrier.wait()
            pipeline.enqueue(_make_event(tid=tid))
            t = pipeline._thread
            if t is not None:
                with seen_lock:
                    seen_threads.append(t)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            unique = {id(t) for t in seen_threads}
            assert len(unique) == 1, f"concurrent first enqueues must yield one thread, got {len(unique)}"
        finally:
            pipeline.stop(drain=False, timeout=5.0)


class TestAtexitDrain:
    """The atexit drain path flushes queued events on shutdown."""

    def test_atexit_drain_flushes_queued_event(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_atexit_drain() delivers events still sitting in the queue.

        Simulates the residual loss window: the process is about to exit with
        events queued. The drain handler (registered from enqueue) must flush
        them to the sender boundary.
        """
        pipeline, captured = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        # Stop the auto-started timer WITHOUT draining so the event is still
        # queued when we invoke the atexit handler -- mirrors process exit
        # before any periodic flush fired.
        pipeline.enqueue(_make_event(tool_name="trw_checkpoint"))
        pipeline.stop(drain=False, timeout=5.0)
        assert len(captured) == 0 or "trw_checkpoint" not in {ev.get("tool_name") for ev in captured}, (
            "precondition: event not yet flushed before drain"
        )

        # Re-queue (stop may have raced a flush); then run the drain handler.
        if not any(ev.get("tool_name") == "trw_checkpoint" for ev in captured):
            pipeline.enqueue(_make_event(tool_name="trw_checkpoint"))

        pipeline._atexit_drain()

        assert any(ev.get("tool_name") == "trw_checkpoint" for ev in captured), (
            "atexit drain must flush the queued event to the sender"
        )

    def test_atexit_registered_once(self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The drain handler is registered exactly once across many enqueues."""
        registrations: list[Any] = []
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.atexit.register",
            lambda fn, *a, **k: registrations.append(fn) or fn,
        )

        pipeline, _ = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        try:
            for i in range(15):
                pipeline.enqueue(_make_event(seq=i))

            drain_regs = [fn for fn in registrations if fn == pipeline._atexit_drain]
            assert len(drain_regs) == 1, f"atexit drain must register once, got {len(drain_regs)}"
        finally:
            pipeline.stop(drain=False, timeout=5.0)

    def test_explicit_stop_unregisters_and_later_enqueue_reregisters(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stopped instance cannot run a duplicate drain after host teardown."""
        registrations: list[Any] = []
        unregistrations: list[Any] = []
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.atexit.register",
            lambda fn, *a, **k: registrations.append(fn) or fn,
        )
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.atexit.unregister",
            lambda fn: unregistrations.append(fn),
        )

        pipeline, _ = _build_pipeline(pipeline_cls, tmp_path, monkeypatch)
        pipeline.enqueue(_make_event(seq=1))
        first_handler = registrations[-1]

        pipeline.stop(drain=False, timeout=5.0)

        assert unregistrations == [first_handler]
        assert pipeline._atexit_registered is False

        # The object remains reusable: enqueue auto-starts it and installs a
        # new fallback rather than silently losing shutdown protection.
        pipeline.enqueue(_make_event(seq=2))
        try:
            assert len(registrations) == 2
            assert registrations[-1] == pipeline._atexit_drain
            assert pipeline._atexit_registered is True
        finally:
            pipeline.stop(drain=False, timeout=5.0)
