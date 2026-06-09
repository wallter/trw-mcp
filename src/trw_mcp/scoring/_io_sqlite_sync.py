"""SQLite write-back helpers for the scoring I/O boundary.

Belongs to the ``_io_boundary.py`` facade. Re-exported there for back-compat
so ``_correlation.py`` keeps a single import point and existing tests that
patch ``trw_mcp.scoring._io_boundary.*`` continue to work.

Holds the best-effort Q-value sync path (single-row and chunk-transactional
batch) that keeps SQLite eventually consistent with the authoritative YAML
entries.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol, cast

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from trw_mcp.scoring._io_boundary import _PendingUpdate

logger = structlog.get_logger(__name__)


class _TransactionalBackend(Protocol):
    """Structural-typed surface of the memory backend used by ``_sync_chunk``.

    PRD-FIX-088 NFR01 ("0 type:ignore added"): scoring previously used
    ``# type: ignore[attr-defined]`` to call ``backend.transaction()`` and
    ``backend.update()`` on an ``object`` parameter. This Protocol pins
    the contract structurally so mypy --strict resolves the calls and the
    ignores are removed.

    The concrete backends (``SQLiteBackend``, the in-memory test double,
    and the YAML pass-through) all implement these two methods today;
    backends without batching expose ``transaction()`` as a no-op
    pass-through context manager.
    """

    def transaction(self) -> AbstractContextManager[object]: ...

    def update(self, entry_id: str, /, **fields: object) -> object: ...


def _sync_to_sqlite(
    lid: str,
    q_new: float,
    q_obs: int,
    history: list[str],
    trw_dir: Path,
) -> None:
    """Sync Q-value and outcome_history back to SQLite (best-effort)."""
    try:
        from trw_mcp.state.memory_adapter import get_backend

        backend = get_backend(trw_dir)
        backend.update(
            lid,
            q_value=round(q_new, 4),
            q_observations=q_obs,  # already incremented by _update_entry_q_values
            outcome_history=history,
        )
    except Exception:  # justified: fail-open, SQLite sync is best-effort (YAML is authoritative)
        logger.debug("q_value_sqlite_sync_skipped", exc_info=True)  # justified: fail-open, YAML is authoritative


# PRD-FIX-088 FR02: Chunk size for ``_batch_sync_to_sqlite`` transaction bracket.
# Each chunk wraps N ``backend.update()`` calls in a single
# ``BEGIN IMMEDIATE`` / ``COMMIT``. Bounds lock-hold-time to ~150 ms/chunk
# (NFR07: ≤ 250 ms p95) so the async sync loop introduced by PRD-FIX-087
# can still interleave between chunks. Tunable without an FR change.
Q_LEARNING_BATCH_CHUNK_SIZE: int = 500


def _batch_sync_to_sqlite(
    updates: list[_PendingUpdate],
    trw_dir: Path,
) -> None:
    """Batch sync Q-values to SQLite in chunked transactions.

    PRD-FIX-070-FR03: groups updates into a single backend session
    instead of N individual calls.

    PRD-FIX-088 FR02: wraps each chunk of ``Q_LEARNING_BATCH_CHUNK_SIZE``
    rows in a single ``BEGIN IMMEDIATE`` / ``COMMIT`` (via
    ``backend.transaction()``). Collapses N implicit transactions to
    ``ceil(N / chunk)`` explicit transactions while bounding the
    SQLite write-lock-hold-time per chunk so the async sync loop is
    not starved.

    Per-row exceptions are caught (existing fail-open behavior preserved);
    a chunk still commits with whatever rows succeeded. WHEN a
    ``BEGIN``/``COMMIT`` itself raises, the chunk is logged at WARNING
    and execution falls through to the next chunk; no exception
    propagates to the caller (Q-learning is best-effort, YAML is
    authoritative).
    """
    if not updates:
        return
    try:
        from trw_mcp.state.memory_adapter import get_backend

        # Cast to the structural protocol since concrete backends define
        # ``transaction``/``update`` (no-op pass-through on backends
        # without batching support); the cast is the documented contract
        # boundary, replacing two ``# type: ignore[attr-defined]``.
        backend = cast("_TransactionalBackend", get_backend(trw_dir))
    except Exception:  # justified: fail-open, SQLite batch sync is best-effort
        logger.debug("q_value_sqlite_batch_sync_failed", exc_info=True)
        return

    total = len(updates)
    expected_chunks = (total + Q_LEARNING_BATCH_CHUNK_SIZE - 1) // Q_LEARNING_BATCH_CHUNK_SIZE
    synced = 0
    for chunk_index in range(expected_chunks):
        start = chunk_index * Q_LEARNING_BATCH_CHUNK_SIZE
        chunk = updates[start : start + Q_LEARNING_BATCH_CHUNK_SIZE]
        chunk_synced = _sync_chunk(backend, chunk, chunk_index, len(chunk))
        synced += chunk_synced
    logger.debug(
        "batch_sqlite_sync_complete",
        synced=synced,
        total=total,
        chunks=expected_chunks,
        chunk_size=Q_LEARNING_BATCH_CHUNK_SIZE,
    )


def _sync_chunk(
    backend: _TransactionalBackend,
    chunk: list[_PendingUpdate],
    chunk_index: int,
    chunk_size: int,
) -> int:
    """Run one transaction-bracketed chunk of ``backend.update()`` calls.

    Returns the number of rows that succeeded. Per-row exceptions are
    caught and logged at debug; a transaction-level failure (BEGIN/COMMIT)
    is caught and logged at WARNING with chunk metadata.
    """
    chunk_synced = 0
    try:
        # backend.transaction() defaults to a no-op pass-through on
        # backends without batching support, so this is safe across
        # SQLite/YAML/in-memory test backends.
        with backend.transaction():
            for lid, _path, _data, q_new, q_obs, history in chunk:
                try:
                    backend.update(
                        lid,
                        q_value=round(q_new, 4),
                        q_observations=q_obs,
                        outcome_history=history,
                    )
                    chunk_synced += 1
                except Exception:  # justified: fail-open, individual entry failures don't abort chunk
                    logger.debug(
                        "q_value_sqlite_sync_skipped",
                        learning_id=lid,
                        exc_info=True,
                    )
    except Exception:  # justified: fail-open, transaction-level failure must not propagate
        logger.warning(
            "batch_sqlite_chunk_failed",
            chunk_index=chunk_index,
            chunk_size=chunk_size,
            exc_info=True,
        )
    return chunk_synced
