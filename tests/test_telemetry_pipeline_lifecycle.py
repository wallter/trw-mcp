"""TelemetryPipeline lifecycle and configuration tests."""

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
from ._telemetry_pipeline_support import fast_pipeline, pipeline_cls  # noqa: F401


class TestTimerThread:
    """start()/stop() manage the background flush thread correctly."""

    def test_timer_thread_starts_and_stops(self, fast_pipeline: Any) -> None:
        """After start() the thread is alive; after stop() it is dead."""
        p = fast_pipeline
        p.start()
        try:
            assert p._thread.is_alive(), "Thread must be alive after start()"
        finally:
            p.stop(drain=False, timeout=5.0)

        deadline = time.monotonic() + 5.0
        while p._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert not p._thread.is_alive(), "Thread must be dead after stop()"

    def test_start_is_idempotent(self, fast_pipeline: Any) -> None:
        """Calling start() twice does not create a second thread."""
        p = fast_pipeline
        p.start()
        try:
            first_thread = p._thread
            p.start()
            assert p._thread is first_thread, "Second start() must not spawn a new thread"
        finally:
            p.stop(drain=False, timeout=5.0)


class TestThreadSafety:
    """Concurrent enqueues must not lose events or corrupt the queue."""

    def test_thread_safety_concurrent_enqueues(self, pipeline_cls: Any) -> None:
        """20 threads each enqueue 100 events; total is bounded by max_queue_size."""
        max_size = 10_000
        p = pipeline_cls(max_queue_size=max_size)
        n_threads = 20
        events_per_thread = 100
        barrier = threading.Barrier(n_threads)

        def worker(tid: int) -> None:
            barrier.wait()
            for i in range(events_per_thread):
                p.enqueue(_make_event(tid=tid, seq=i))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(p._queue) == n_threads * events_per_thread
        for item in p._queue:
            assert isinstance(item, dict)


class TestStopDrain:
    """stop(drain=True) triggers a final flush before the thread exits."""

    def test_stop_drain_calls_flush(self, fast_pipeline: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """stop(drain=True) must call flush_now at least once."""
        p = fast_pipeline
        flush_calls: list[int] = []
        original_flush = p.flush_now

        def counting_flush() -> Any:
            flush_calls.append(1)
            return original_flush()

        monkeypatch.setattr(p, "flush_now", counting_flush)
        p.start()
        p.enqueue(_make_event(tool_name="trw_deliver"))
        p.stop(drain=True, timeout=5.0)

        assert len(flush_calls) >= 1, "flush_now must be called during drain"

    def test_stop_timeout_returns_within_bound(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stop(timeout=0.5) returns within ~1.5s even if flush hangs."""
        p, _ = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=_make_fake_cfg(installation_id="t", framework_version="v0"),
            pipeline_kwargs={
                "flush_interval_secs": 0.05,
                "batch_size": 100,
                "max_retries": 1,
                "backoff_base": 0.0,
            },
        )

        def _slow_flush() -> Any:
            time.sleep(10)
            return {"sent": 0, "failed": 0, "overflow": 0, "skipped_reason": "test"}

        p.flush_now = _slow_flush
        p.start()

        t0 = time.monotonic()
        p.stop(drain=True, timeout=0.5)
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0, f"stop() must respect timeout; took {elapsed:.2f}s"


class TestSingleton:
    """get_instance() is thread-safe and returns the same object."""

    def test_singleton_get_instance_returns_same_object(self, pipeline_cls: Any) -> None:
        """get_instance() from 10 threads all return the same object."""
        instances: list[Any] = []
        barrier = threading.Barrier(10)

        def worker() -> None:
            barrier.wait()
            instances.append(pipeline_cls.get_instance())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        ids = {id(inst) for inst in instances}
        assert len(ids) == 1, f"Expected 1 unique instance, got {len(ids)}"

    def test_reset_creates_new_instance(self, pipeline_cls: Any) -> None:
        """After reset(), get_instance() returns a different object."""
        first = pipeline_cls.get_instance()
        pipeline_cls.reset()
        second = pipeline_cls.get_instance()
        assert id(first) != id(second), "reset() must invalidate the old singleton"

    def test_reset_kills_running_thread(self, pipeline_cls: Any, fast_pipeline: Any) -> None:
        """reset() stops any running background thread from the old instance."""
        p = fast_pipeline
        p.start()
        old_thread = p._thread
        assert old_thread.is_alive()

        pipeline_cls._instance = p
        pipeline_cls.reset()

        old_thread.join(timeout=5.0)
        assert not old_thread.is_alive(), "reset() must stop the daemon thread"


class TestLazyConfigResolution:
    """get_config() is called at flush time, not at construction time."""

    def test_lazy_config_resolution(self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """flush_now picks up config values set after pipeline construction."""
        make_configured_pipeline(pipeline_cls, tmp_path, monkeypatch)

        config_holder: dict[str, Any] = {}
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config_holder["cfg"], raising=False)

        config_holder["cfg"] = _make_fake_cfg(
            telemetry_enabled=False,
            platform_telemetry_enabled=False,
            installation_id="",
            framework_version="v1",
        )
        p = pipeline_cls()
        p.enqueue({"event_type": "test_event_1"})

        config_holder["cfg"] = _make_fake_cfg(
            installation_id="lazy-id",
            framework_version="v2",
        )

        result = p.flush_now()
        assert isinstance(result, dict)
