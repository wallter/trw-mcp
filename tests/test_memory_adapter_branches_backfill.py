"""Targeted memory adapter backfill branch tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_memory.models.memory import MemoryEntry

from trw_mcp.state.memory_adapter import backfill_embeddings, get_backend, store_learning

from ._memory_adapter_branches_support import trw_dir  # noqa: F401


class TestBackfillEmbeddings:
    def test_no_embedder_returns_zeros(self, trw_dir: Path) -> None:
        """When embedder is None, returns all zeros (lines 750-751)."""
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            result = backfill_embeddings(trw_dir)
            assert result == {"embedded": 0, "skipped": 0, "failed": 0}

    def test_embeds_all_entries(self, trw_dir: Path) -> None:
        """Backfills embeddings for all entries (lines 753-775)."""
        store_learning(trw_dir, "L-bf1", "Alpha backfill", "Detail alpha")
        store_learning(trw_dir, "L-bf2", "Beta backfill", "Detail beta")

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

        backend = get_backend(trw_dir)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "upsert_vector") as mock_upsert,
            patch.object(backend, "existing_vector_ids", return_value=set()),
        ):
            result = backfill_embeddings(trw_dir)
            assert result["embedded"] == 2
            assert result["skipped"] == 0
            assert result["failed"] == 0
            assert mock_upsert.call_count == 2

    def test_short_circuits_when_all_entries_already_embedded(self, trw_dir: Path) -> None:
        """When vector count covers all entries, list_entries() is NOT called.

        Regression for the n=6438 case where every entry already had a vector
        but list_entries(limit=N) still loaded + Pydantic-validated all rows
        (~27s wasted per session_start). The short-circuit must fire from
        cheap COUNT/SELECT-id queries alone, never touching list_entries.
        """
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        backend = get_backend(trw_dir)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "existing_vector_ids", return_value={f"L-{i}" for i in range(5)}),
            patch.object(backend, "count", return_value=5),
            patch.object(backend, "list_entries") as mock_list,
            patch.object(backend, "upsert_vector") as mock_upsert,
        ):
            result = backfill_embeddings(trw_dir)
            assert result == {"embedded": 0, "skipped": 5, "failed": 0}
            mock_list.assert_not_called()
            mock_embedder.embed.assert_not_called()
            mock_upsert.assert_not_called()

    def test_skips_already_embedded_entries(self, trw_dir: Path) -> None:
        """Already-embedded entries are skipped without calling embed.

        Regression for the bug where backfill_embeddings re-embedded every
        entry on every call (no idempotency check), causing ~23 min of
        synchronous work inside trw_session_start on a 6437-entry corpus.
        """
        store_learning(trw_dir, "L-already-1", "Existing 1", "Detail 1")
        store_learning(trw_dir, "L-already-2", "Existing 2", "Detail 2")
        store_learning(trw_dir, "L-fresh", "Needs embed", "Detail 3")

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

        backend = get_backend(trw_dir)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "upsert_vector") as mock_upsert,
            patch.object(
                backend,
                "existing_vector_ids",
                return_value={"L-already-1", "L-already-2"},
            ),
        ):
            result = backfill_embeddings(trw_dir)
            assert result["embedded"] == 1
            assert result["skipped"] == 2
            assert result["failed"] == 0
            assert mock_embedder.embed.call_count == 1
            assert mock_upsert.call_count == 1

    def test_skips_empty_content(self, trw_dir: Path) -> None:
        """Entries with empty content+detail are skipped (lines 765-767)."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-sk1", content="", detail=""))

        mock_embedder = MagicMock()

        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = backfill_embeddings(trw_dir)
            assert result["skipped"] == 1
            mock_embedder.embed.assert_not_called()

    def test_embed_returns_none_counts_as_failed(self, trw_dir: Path) -> None:
        """When embed returns None, increments failed (lines 770-772).

        ``existing_vector_ids`` is stubbed empty so the entry is treated as
        un-embedded and reaches the backfill embed path. Since embeddings_enabled
        now defaults to True (commit f4ca661c9), ``store_learning`` embeds the
        entry synchronously and the idempotency skip (commit f57fc6615) would
        otherwise short it out before the failed++ branch under test.
        """
        store_learning(trw_dir, "L-fn1", "Fail none", "Detail")

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = None

        backend = get_backend(trw_dir)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "existing_vector_ids", return_value=set()),
        ):
            result = backfill_embeddings(trw_dir)
            assert result["failed"] == 1

    def test_exception_counts_as_failed(self, trw_dir: Path) -> None:
        """When embedding raises, increments failed (lines 776-777).

        ``existing_vector_ids`` is stubbed empty for the same reason as
        :meth:`test_embed_returns_none_counts_as_failed`: store-time embedding
        (embeddings_enabled default True) plus the backfill idempotency skip
        would otherwise bypass the exception failed++ branch under test.
        """
        store_learning(trw_dir, "L-fe1", "Fail exception", "Detail")

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("embed error")

        backend = get_backend(trw_dir)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "existing_vector_ids", return_value=set()),
        ):
            result = backfill_embeddings(trw_dir)
            assert result["failed"] == 1
