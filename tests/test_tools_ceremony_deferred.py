"""Tests for deferred delivery locking and launcher helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.tools._deferred_delivery import (
    _launch_deferred,
    _log_deferred_result,
    _release_deferred_lock,
    _run_deferred_steps,
    _try_acquire_deferred_lock,
)


def test_deferred_consolidation_forbids_cold_embedder_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delivery maintenance must not cold-load heavy embedding runtimes in-server."""
    from trw_mcp.tools import _deferred_steps_memory as memory_steps

    captured: dict[str, object] = {}

    def fake_consolidate_cycle(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "no_clusters"}

    monkeypatch.setattr("trw_mcp.state.consolidation.consolidate_cycle", fake_consolidate_cycle)
    result = memory_steps._step_consolidation(tmp_path / ".trw")

    assert result["status"] == "no_clusters"
    assert captured["allow_cold_embedder_load"] is False


class TestDeferredLock:
    """Non-blocking file lock prevents concurrent deferred batches."""

    def test_acquire_and_release(self, tmp_path: Path) -> None:
        """Lock can be acquired and released cleanly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd = _try_acquire_deferred_lock(trw_dir)
        assert fd is not None
        _release_deferred_lock(fd)

    def test_second_acquire_fails_while_held(self, tmp_path: Path) -> None:
        """Second acquire returns None while first lock is held."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd1 = _try_acquire_deferred_lock(trw_dir)
        assert fd1 is not None
        try:
            fd2 = _try_acquire_deferred_lock(trw_dir)
            assert fd2 is None, "Should not acquire lock while held"
        finally:
            _release_deferred_lock(fd1)

    def test_second_acquire_preserves_lock_holder_record_while_held(self, tmp_path: Path) -> None:
        """A contending acquire must not truncate the active holder record."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd1 = _try_acquire_deferred_lock(trw_dir)
        assert fd1 is not None
        lock_path = trw_dir / "deliver-deferred.lock"
        record_before = lock_path.read_text(encoding="utf-8")
        assert '"pid"' in record_before
        try:
            fd2 = _try_acquire_deferred_lock(trw_dir)
            assert fd2 is None
            assert lock_path.read_text(encoding="utf-8") == record_before
        finally:
            _release_deferred_lock(fd1)

    def test_reacquire_after_release(self, tmp_path: Path) -> None:
        """Lock can be re-acquired after release."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd1 = _try_acquire_deferred_lock(trw_dir)
        assert fd1 is not None
        _release_deferred_lock(fd1)

        fd2 = _try_acquire_deferred_lock(trw_dir)
        assert fd2 is not None
        _release_deferred_lock(fd2)


class TestDeferredLogResult:
    """Deferred results are logged to an audit file."""

    def test_writes_jsonl_entry(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        results = {"consolidation": {"status": "success"}}
        errors: list[str] = []
        _log_deferred_result(trw_dir, results, errors)

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["success"] is True
        assert "consolidation" in entry["results"]

    def test_logs_errors_gracefully(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _log_deferred_result(trw_dir, {}, ["consolidation: boom"])

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["success"] is False
        assert "consolidation: boom" in entry["errors"]


class TestRunDeferredSteps:
    """Deferred steps execute with fail-open semantics and file locking."""

    def test_skips_when_lock_held(self, tmp_path: Path) -> None:
        """If lock is already held, deferred steps skip entirely."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd = _try_acquire_deferred_lock(trw_dir)
        assert fd is not None
        try:
            _run_deferred_steps(trw_dir, None, {})
            log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
            assert not log_path.exists()
        finally:
            _release_deferred_lock(fd)

    def test_all_steps_fail_open(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each deferred step can fail without blocking others."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        step_names = [
            "_step_auto_prune",
            "_step_consolidation",
            "_step_tier_sweep",
            "_do_index_sync",
            "_step_auto_progress",
            "_step_publish_learnings",
            "_step_outcome_correlation",
            "_step_recall_outcome",
            "_step_telemetry",
            "_step_batch_send",
            "_step_trust_increment",
            "_step_ceremony_feedback",
        ]
        for name in step_names:
            monkeypatch.setattr(
                f"trw_mcp.tools._deferred_delivery.{name}",
                lambda *args, name=name, **kwargs: (_ for _ in ()).throw(Exception(f"{name} boom")),
            )

        _run_deferred_steps(trw_dir, None, {})

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["success"] is False
        assert len(entry["errors"]) > 0


class TestLaunchDeferred:
    """Background thread launcher with deduplication."""

    def test_returns_launched(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Launching deferred steps returns 'launched'."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        step_names = [
            "_step_auto_prune",
            "_step_consolidation",
            "_step_tier_sweep",
            "_do_index_sync",
            "_step_auto_progress",
            "_step_publish_learnings",
            "_step_outcome_correlation",
            "_step_recall_outcome",
            "_step_telemetry",
            "_step_batch_send",
            "_step_trust_increment",
            "_step_ceremony_feedback",
        ]
        for name in step_names:
            monkeypatch.setattr(
                f"trw_mcp.tools._deferred_delivery.{name}",
                lambda *args, **kwargs: {"status": "mocked"},
            )

        import trw_mcp.tools._deferred_state as _ds

        monkeypatch.setattr(_ds, "_deferred_thread", None)

        status = _launch_deferred(trw_dir, None, {})
        assert status == "launched"

        with _ds._deferred_lock:
            if _ds._deferred_thread is not None:
                _ds._deferred_thread.join(timeout=10)

    def test_skips_when_thread_alive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Second launch returns 'skipped_already_running' while first is active."""
        import threading

        import trw_mcp.tools._deferred_state as _ds

        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)
        barrier = threading.Event()

        def slow_worker() -> None:
            barrier.wait(timeout=10)

        fake_thread = threading.Thread(target=slow_worker, daemon=True)
        fake_thread.start()
        monkeypatch.setattr(_ds, "_deferred_thread", fake_thread)

        try:
            status = _launch_deferred(trw_dir, None, {})
            assert status == "skipped_already_running"
        finally:
            barrier.set()
            fake_thread.join(timeout=5)


class TestDeferredAtexitJoin:
    """PRD-FIX-088: the atexit hook must flush an in-flight deferred batch so
    daemon-thread mid-write data loss cannot drop pending learning work."""

    def test_atexit_join_flushes_inflight_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The atexit hook joins the live deferred thread so its pending work
        runs to completion before interpreter exit (no silent loss)."""
        import threading

        import trw_mcp.tools._deferred_delivery as _dd
        import trw_mcp.tools._deferred_state as _ds

        completed = threading.Event()
        release = threading.Event()

        def worker() -> None:
            # Block until the test releases us, then mark work done. The atexit
            # join must wait for this completion rather than abandon it.
            release.wait(timeout=10)
            completed.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        monkeypatch.setattr(_ds, "_deferred_thread", thread)

        # Release the worker, then invoke the atexit hook: it must join and
        # observe the work as completed.
        release.set()
        _dd._join_deferred_thread_at_exit()

        assert completed.is_set(), "atexit hook returned before deferred work finished"
        assert not thread.is_alive(), "deferred thread should be joined after atexit hook"

    def test_atexit_join_bounded_when_thread_stuck(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuinely stuck deferred step must not wedge interpreter exit —
        the join is bounded by a timeout and returns even if the thread runs on."""
        import threading

        import trw_mcp.tools._deferred_delivery as _dd
        import trw_mcp.tools._deferred_state as _ds

        stuck = threading.Event()
        try:
            thread = threading.Thread(target=lambda: stuck.wait(timeout=30), daemon=True)
            thread.start()
            monkeypatch.setattr(_ds, "_deferred_thread", thread)
            monkeypatch.setattr(_dd, "_DEFERRED_ATEXIT_JOIN_TIMEOUT_S", 0.1)

            # Must return promptly despite the thread still running.
            _dd._join_deferred_thread_at_exit()
            assert thread.is_alive(), "thread should still be running (proving the join was bounded)"
        finally:
            stuck.set()
            thread.join(timeout=5)

    def test_launch_registers_atexit_hook_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_launch_deferred`` registers the flush hook with ``atexit`` exactly
        once per process even across repeated launches."""
        import trw_mcp.tools._deferred_delivery as _dd
        import trw_mcp.tools._deferred_state as _ds

        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        # No-op the orchestrator body so the launched threads finish instantly;
        # this test only asserts the atexit-registration behavior.
        monkeypatch.setattr(_dd, "_run_deferred_steps", lambda *a, **k: {})

        registered: list[object] = []
        monkeypatch.setattr(_dd.atexit, "register", lambda fn: registered.append(fn))
        # Reset the one-shot flag so this test exercises a fresh registration.
        monkeypatch.setattr(_dd, "_atexit_join_registered", False)
        monkeypatch.setattr(_ds, "_deferred_thread", None)

        _launch_deferred(trw_dir, None, {})
        with _ds._deferred_lock:
            if _ds._deferred_thread is not None:
                _ds._deferred_thread.join(timeout=10)
        monkeypatch.setattr(_ds, "_deferred_thread", None)
        _launch_deferred(trw_dir, None, {})
        with _ds._deferred_lock:
            if _ds._deferred_thread is not None:
                _ds._deferred_thread.join(timeout=10)

        assert len(registered) == 1, f"expected exactly one atexit registration, got {len(registered)}"
