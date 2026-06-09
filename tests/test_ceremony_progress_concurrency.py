"""Concurrency tests for ceremony progress state read-modify-write mutators.

In shared-HTTP mode multiple MCP tool calls run on different threads of one
process. Each ceremony mutator does an unguarded read -> mutate -> write of
ceremony-state.json; without serialization concurrent calls drop one another's
updates. The per-state-path lock (``_state_rmw``) must make every mutator's
RMW cycle atomic so no increment is lost.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from trw_mcp.state import _ceremony_progress_state as cps


def _trw(tmp_path: Path) -> Path:
    trw = tmp_path / ".trw"
    (trw / "context").mkdir(parents=True, exist_ok=True)
    return trw


def test_concurrent_increment_learnings_no_lost_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N threads each increment learnings M times -> final count is exactly N*M.

    The original code (read -> mutate -> write outside any lock) drops updates
    under contention. To make the lost-update window deterministic, widen the
    read-write gap with a thread yield inside the wrapped read; the lock must
    still serialize so the total is exact.
    """
    trw = _trw(tmp_path)

    real_read = cps.read_ceremony_state

    def slow_read(path: Path) -> cps.CeremonyState:
        state = real_read(path)
        # Yield inside the critical section to maximize the chance that an
        # unserialized competitor would read the same stale value.
        time_yield()
        return state

    def time_yield() -> None:
        # threading switch interval is ~5ms; a bare sleep(0) forces a GIL
        # handoff which is enough to expose a missing lock.
        import time

        time.sleep(0)

    monkeypatch.setattr(cps, "read_ceremony_state", slow_read)

    threads_count = 8
    per_thread = 25
    barrier = threading.Barrier(threads_count)

    def worker() -> None:
        barrier.wait()
        for _ in range(per_thread):
            cps.increment_learnings(trw)

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    final = real_read(trw)
    assert final.learnings_this_session == threads_count * per_thread, (
        f"lost updates: expected {threads_count * per_thread}, "
        f"got {final.learnings_this_session}"
    )


def test_concurrent_mixed_mutators_preserve_each_field(
    tmp_path: Path,
) -> None:
    """Concurrent checkpoints and tool-call increments each land exactly once."""
    trw = _trw(tmp_path)

    n = 50
    barrier = threading.Barrier(2)

    def checkpoints() -> None:
        barrier.wait()
        for _ in range(n):
            cps.mark_checkpoint(trw)

    def counters() -> None:
        barrier.wait()
        for _ in range(n):
            cps.increment_tool_call_counter(trw)

    t1 = threading.Thread(target=checkpoints)
    t2 = threading.Thread(target=counters)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    final = cps.read_ceremony_state(trw)
    assert final.checkpoint_count == n, f"checkpoints lost: {final.checkpoint_count}"
    assert final.tool_call_counter == n, f"tool-call increments lost: {final.tool_call_counter}"


def test_state_lock_is_shared_per_resolved_path(tmp_path: Path) -> None:
    """Different spellings of the same trw_dir share one lock (keyed on the
    resolved state-file path) so all callers serialize correctly."""
    trw = _trw(tmp_path)
    aliased = trw.parent / "." / ".trw"  # same dir, different spelling

    lock_a = cps._state_lock_for(trw)
    lock_b = cps._state_lock_for(aliased)
    assert lock_a is lock_b, "same state file must map to the same lock"
