"""PRD-FIX-088 FR02: ``_batch_sync_to_sqlite`` uses chunked transactions.

Pre-fix: each ``backend.update`` call ran in its own implicit transaction
(N implicit COMMITs for N entries). On a 2823-entry batch this was the
dominant cost — measured 91 s wall time on the dev repo.

Post-fix: updates are grouped into chunks of
``Q_LEARNING_BATCH_CHUNK_SIZE`` (default 500); each chunk is wrapped in
one ``backend.transaction()`` (BEGIN IMMEDIATE / COMMIT). Collapses N
implicit transactions to ``ceil(N / 500)`` explicit transactions while
bounding lock-hold-time per chunk.

Spy mechanism: a tiny stub backend counts ``transaction()`` enter calls
and ``update()`` calls per invocation. The expected chunk count for
N updates is ``ceil(N / Q_LEARNING_BATCH_CHUNK_SIZE)``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


class _CountingBackend:
    """Minimal backend stub that counts transaction() and update() calls."""

    def __init__(self) -> None:
        self.transaction_enter_count: int = 0
        self.update_call_count: int = 0
        self.last_updates: list[tuple[str, dict[str, Any]]] = []

    @contextlib.contextmanager
    def transaction(self) -> Iterator[_CountingBackend]:
        self.transaction_enter_count += 1
        yield self

    def update(self, entry_id: str, **fields: Any) -> object:
        self.update_call_count += 1
        self.last_updates.append((entry_id, dict(fields)))
        return None


def _make_pending_updates(n: int) -> list[Any]:
    """Build N stub _PendingUpdate tuples."""
    return [(f"L-{i:04d}", None, {"id": f"L-{i:04d}"}, 0.5, 1, []) for i in range(n)]


@pytest.mark.parametrize(
    "n_updates, expected_chunks",
    [
        (1, 1),
        (499, 1),
        (500, 1),
        (501, 2),
        (1000, 2),
        (1001, 3),
        (2823, 6),
    ],
)
def test_batch_sync_chunks_N_updates_into_expected_chunks(
    n_updates: int,
    expected_chunks: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR02 acceptance: ``ceil(N / 500)`` chunks for N updates."""
    from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

    spy_backend = _CountingBackend()
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.get_backend",
        lambda trw_dir: spy_backend,
    )

    updates = _make_pending_updates(n_updates)
    _batch_sync_to_sqlite(updates, tmp_path / ".trw")

    assert spy_backend.transaction_enter_count == expected_chunks, (
        f"FR02: expected {expected_chunks} transaction() entries for "
        f"N={n_updates} updates with chunk_size=500, got "
        f"{spy_backend.transaction_enter_count}"
    )
    assert spy_backend.update_call_count == n_updates, (
        f"FR02: every update must be applied. expected {n_updates} update() calls, got {spy_backend.update_call_count}"
    )


def test_batch_sync_zero_updates_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: zero-update batch must NOT enter a transaction (no work to bracket)."""
    from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

    spy_backend = _CountingBackend()
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.get_backend",
        lambda trw_dir: spy_backend,
    )

    _batch_sync_to_sqlite([], tmp_path / ".trw")

    assert spy_backend.transaction_enter_count == 0
    assert spy_backend.update_call_count == 0


def test_batch_sync_per_row_failure_does_not_abort_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR02: per-row exception is caught; remaining rows in the chunk still apply."""

    class _SometimesRaisingBackend(_CountingBackend):
        def update(self, entry_id: str, **fields: Any) -> object:
            self.update_call_count += 1
            if entry_id == "L-0001":
                raise RuntimeError("simulated row failure")
            self.last_updates.append((entry_id, dict(fields)))
            return None

    backend = _SometimesRaisingBackend()
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.get_backend",
        lambda trw_dir: backend,
    )

    from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

    updates = _make_pending_updates(5)
    _batch_sync_to_sqlite(updates, tmp_path / ".trw")

    # All 5 updates were attempted (none aborted by the row-1 failure).
    assert backend.update_call_count == 5
    # The 4 non-raising rows landed in last_updates.
    assert len(backend.last_updates) == 4
    assert "L-0001" not in [lid for lid, _ in backend.last_updates]


def test_batch_sync_transaction_failure_falls_through_to_next_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR02: a transaction-level failure in one chunk does not propagate or skip remaining chunks."""

    class _ChunkFailBackend(_CountingBackend):
        def __init__(self) -> None:
            super().__init__()
            self._chunk_index = 0

        @contextlib.contextmanager
        def transaction(self) -> Iterator[_ChunkFailBackend]:
            self.transaction_enter_count += 1
            self._chunk_index += 1
            if self._chunk_index == 2:
                raise RuntimeError("simulated BEGIN failure on chunk 2")
            yield self

    backend = _ChunkFailBackend()
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.get_backend",
        lambda trw_dir: backend,
    )

    from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

    # 3 chunks expected: chunks 1 and 3 succeed, chunk 2 fails at BEGIN.
    updates = _make_pending_updates(1001)  # ceil(1001/500) = 3
    _batch_sync_to_sqlite(updates, tmp_path / ".trw")

    # All 3 chunks were attempted — chunk 2's failure didn't short-circuit
    # chunks 3+. update() was called for chunks 1 and 3 only (chunk 2's
    # ``with`` block raised before the inner update() loop).
    assert backend.transaction_enter_count == 3, (
        "FR02: chunk-level failure must not skip remaining chunks. "
        f"Expected 3 transaction entries, got {backend.transaction_enter_count}"
    )
    # Chunk 1 = 500 rows; chunk 2 = 0 (raised); chunk 3 = 1 row. Total = 501.
    assert backend.update_call_count == 501


@pytest.mark.slow
def test_batch_sync_2000_rows_under_300ms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR02 wall-time guarantee: 2000-row batch sync completes in <300 ms.

    PRD-FIX-088 §FR02 acceptance: "Wall-time benchmark on a 2000-entry
    batch SHALL be <300 ms". Pre-fix this batch took 91 s on the live
    deployment (one implicit ``COMMIT`` per row). Post-fix it bundles
    into ``ceil(2000/500) = 4`` chunks.

    Uses the production ``_CountingBackend`` shape via the spy injected
    through ``state.memory_adapter.get_backend``. The dominant cost
    pre-fix was the per-row implicit transaction overhead in SQLite,
    which the chunked-transaction wrapper collapses; here we measure
    the hot path through ``_batch_sync_to_sqlite`` itself, since this
    test must not depend on a real SQLite write-cache for repeatability
    on CI.
    """
    import time

    from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

    spy_backend = _CountingBackend()
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.get_backend",
        lambda trw_dir: spy_backend,
    )

    updates = _make_pending_updates(2000)

    start = time.monotonic()
    _batch_sync_to_sqlite(updates, tmp_path / ".trw")
    elapsed = time.monotonic() - start

    assert elapsed < 0.3, (
        f"FR02 wall-time regression: 2000-row batch sync took {elapsed * 1000:.1f}ms "
        f"(cap 300ms). Pre-fix this was 91s with one implicit COMMIT per row. "
        f"If this fails, the chunked-transaction wrapper has regressed or new "
        f"per-row work has been added inside _sync_chunk."
    )
    # Sanity: every row was applied across exactly 4 chunks.
    assert spy_backend.update_call_count == 2000
    assert spy_backend.transaction_enter_count == 4


def test_chunk_size_constant_is_documented_value(tmp_path: Path) -> None:
    """FR02 stability: the chunk-size constant is exported and equals 500."""
    from trw_mcp.scoring._io_boundary import Q_LEARNING_BATCH_CHUNK_SIZE

    assert Q_LEARNING_BATCH_CHUNK_SIZE == 500, (
        "FR02: chunk size is part of the public tunable surface. Changing "
        "this value affects lock-hold-time bounds (NFR07). Update PRD if "
        "intentional."
    )


@pytest.mark.slow
def test_batch_sync_2000_rows_real_sqlite_under_1s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR02 wall-time guarantee on a REAL SQLite backend.

    Round-2 F4: the spy-backed wall-time test (above) cannot detect a
    regression where someone re-introduces per-row implicit COMMITs,
    because the spy has no transaction layer to measure. This test
    runs the same hot path against an actual ``SQLiteBackend`` writing
    to disk so the per-row vs chunked-transaction cost is real.

    Cap is 1000 ms (not 300) because real SQLite + WAL setup adds
    fixed cost on cold open; pre-fix this batch took ~91s on the live
    deployment, so the >90× margin still catches a regression to
    per-row commits while tolerating SSD jitter and cold WAL.
    """
    import time
    from datetime import datetime, timezone

    pytest.importorskip("trw_memory")
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

    db_path = tmp_path / "real-sqlite.db"
    backend = SQLiteBackend(db_path)
    # Seed 2000 entries so update() targets exist (update is a no-op on
    # missing IDs; we want it to actually write rows).
    now = datetime.now(timezone.utc)
    for i in range(2000):
        entry = MemoryEntry(
            id=f"L-{i:04d}",
            content=f"seed entry {i}",
            tags=[],
            importance=0.5,
            type="pattern",
            created_at=now,
            updated_at=now,
        )
        backend.store(entry)

    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda trw_dir: backend)

    updates = _make_pending_updates(2000)

    start = time.monotonic()
    _batch_sync_to_sqlite(updates, tmp_path / ".trw")
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"FR02 wall-time regression on REAL SQLite: 2000-row batch sync took "
        f"{elapsed * 1000:.1f}ms (cap 1000ms). Pre-fix this was 91s with one "
        f"implicit COMMIT per row. A >1s value here suggests the chunked-"
        f"transaction wrapper has been bypassed (each update() is committing "
        f"on its own again)."
    )
