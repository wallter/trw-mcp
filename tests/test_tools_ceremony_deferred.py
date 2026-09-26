"""Tests for deferred delivery locking and launcher helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._deferred_delivery import (
    _launch_deferred,
    _log_deferred_result,
    _release_deferred_lock,
    _run_deferred_steps,
    _try_acquire_deferred_lock,
)


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
            "_step_tier_sweep",
            "_do_index_sync",
            "_step_auto_progress",
            "_step_publish_learnings",
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
            "_step_tier_sweep",
            "_do_index_sync",
            "_step_auto_progress",
            "_step_publish_learnings",
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


class TestMemoryDecayStep:
    """PRD-CORE-244 FR09 — importance decay has a production caller.

    ``memory_decay_pass`` was once hardened and tested with ZERO production
    callers. The step now runs the store's ``maintain`` (PRD-CORE-280), so what
    the pass decays -- aged rows only, whatever their ``cross_validated``, at
    most a batch -- is trw-memory's to test (``tests/test_graph_decay.py``).
    These pin how the step reports the pass it asked for.
    """

    def test_the_decay_pass_counts_are_the_step_result(
        self, tmp_path: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._deferred_steps_memory import _step_memory_decay

        consolidation = {"status": "ok", "scope": "namespace", "clusters_found": 1, "entries_consolidated": 3}
        passes = {"decay": {"status": "ok", "processed": 2, "remaining": 1}, "consolidation": consolidation}
        monkeypatch.setattr(
            fake_memory_store, "maintain", lambda _namespace, _policy: {"status": "ok", "passes": passes}
        )

        result = _step_memory_decay(tmp_path / ".trw")

        # Consolidation runs in the daemon's maintain; the delivery reports it (PRD-CORE-302 FR03).
        assert result == {
            "status": "success",
            "reason": "",
            "processed": 2,
            "remaining": 1,
            "consolidation": consolidation,
        }

    def test_a_decay_pass_the_store_skipped_is_a_skipped_step(
        self, tmp_path: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._deferred_steps_memory import _step_memory_decay

        passes = {"decay": {"status": "skipped", "reason": "locked"}}
        monkeypatch.setattr(
            fake_memory_store, "maintain", lambda _namespace, _policy: {"status": "ok", "passes": passes}
        )

        result = _step_memory_decay(tmp_path / ".trw")

        assert (result["status"], result["reason"], result["processed"]) == ("skipped", "locked", 0)

    def test_the_step_maintains_the_checkouts_namespace_under_its_policy(
        self, tmp_path: Path, fake_memory_store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests._memory_fixtures import FAKE_NAMESPACE
        from trw_mcp.tools._deferred_steps_memory import _step_memory_decay

        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: TRWConfig(
                memory_consolidation_enabled=False,
                memory_consolidation_similarity_threshold=0.9,
                memory_consolidation_min_cluster=4,
                memory_consolidation_max_per_cycle=7,
            ),
        )

        _step_memory_decay(tmp_path / ".trw")

        # The daemon's config serves every project, so this project's policy travels with the request.
        policy = {"enabled": False, "similarity_threshold": 0.9, "min_cluster": 4, "max_per_cycle": 7}
        assert ("maintain", (FAKE_NAMESPACE, policy)) in fake_memory_store.calls

    def test_decay_is_wired_into_the_deferred_roster(self) -> None:
        """FR09 half one: a real production call site, not a library function."""
        from trw_mcp.tools._deferred_delivery import DEFERRED_STEPS
        from trw_mcp.tools._delivery_tracer import DEFERRED_STEP_EFFECT_IDS

        assert "memory_decay" in DEFERRED_STEPS
        # It runs AFTER the tier sweep so a row demoted this delivery is not also
        # decayed in the same pass.
        assert DEFERRED_STEPS.index("memory_decay") > DEFERRED_STEPS.index("tier_sweep")
        assert DEFERRED_STEP_EFFECT_IDS["memory_decay"] == "D25"


def test_a_failed_maintenance_pass_is_an_error_even_when_decay_succeeds(
    tmp_path: Path, fake_memory_store: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon's maintain runs several passes; any failed one surfaces, by name."""
    from trw_mcp.tools._deferred_steps_memory import _step_memory_decay

    passes = {"decay": {"status": "ok", "processed": 2, "remaining": 0}, "consolidation": {"status": "error"}}
    monkeypatch.setattr(
        fake_memory_store, "maintain", lambda _namespace, _policy: {"status": "error", "passes": passes}
    )

    result = _step_memory_decay(tmp_path / ".trw")

    assert (result["status"], result["reason"]) == ("error", "failed passes: consolidation")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            {
                "status": "ok",
                "passes": {"decay": {"status": "ok"}, "verification": {"status": "ok", "complete": False}},
            },
            ("success", "verification continues next delivery"),
        ),
        ({"status": "busy", "error": "memory_maintain is already running for ns"}, ("skipped", "busy")),
    ],
    ids=["partial-sweep", "busy"],
)
def test_a_bounded_or_busy_maintain_is_reported_as_such(
    tmp_path: Path, fake_memory_store: object, monkeypatch: pytest.MonkeyPatch, answer, expected
) -> None:
    """rc9: the daemon's maintain verifies a large namespace over several calls, and refuses a second
    concurrent maintain of one namespace; neither is a completed maintenance, neither is a failure."""
    from trw_mcp.tools._deferred_steps_memory import _step_memory_decay

    monkeypatch.setattr(fake_memory_store, "maintain", lambda _namespace, _policy: answer)

    result = _step_memory_decay(tmp_path / ".trw")

    assert (result["status"], result["reason"]) == expected


def test_a_capped_decay_count_is_reported_as_a_lower_bound(
    tmp_path: Path, fake_memory_store: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc9: the store counts qualifying rows only up to a cap; the step says so rather than reporting
    the capped number as exact."""
    from trw_mcp.tools._deferred_steps_memory import _step_memory_decay

    decay = {"status": "ok", "processed": 1000, "remaining": 9000, "remaining_capped": True}
    monkeypatch.setattr(
        fake_memory_store, "maintain", lambda _namespace, _policy: {"status": "ok", "passes": {"decay": decay}}
    )

    result = _step_memory_decay(tmp_path / ".trw")

    assert (result["remaining"], result.get("remaining_capped")) == (9000, True)
