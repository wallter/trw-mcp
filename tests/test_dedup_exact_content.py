"""Behavior tests for embedding-independent exact-content dedup (PRD-CORE-042).

Root-cause fix: ``check_duplicate`` short-circuited to "store" on every default
install because ``embeddings_enabled`` defaults to False, so byte-identical
re-learns accumulated as exact duplicates. The exact-content check now runs
BEFORE the embeddings gate and returns "merge" on an exact backend hit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import check_duplicate
from trw_mcp.state.persistence import FileStateReader


class TestExactContentDedupBypassesEmbeddingsGate:
    """The exact-content path must run even when embeddings are disabled."""

    def test_identical_content_dedups_with_embeddings_disabled(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """A byte-identical re-learn is deduped EVEN WITH embeddings disabled.

        This is the root-cause proof: before the fix, embeddings_enabled=False
        returned "store" unconditionally and the dupe was accumulated.
        """
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        # Embeddings explicitly OFF — the default-install failure mode.
        config = TRWConfig(embeddings_enabled=False)

        mock_backend = MagicMock()
        mock_backend.find_active_by_content.return_value = "L-existing-exact"

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = check_duplicate(
                "exact duplicate summary",
                "exact duplicate detail",
                entries_dir,
                reader,
                config=config,
            )

        # Merge (not skip) so the new entry's tags/evidence/impact still fold in.
        assert result.action == "merge"
        assert result.existing_id == "L-existing-exact"
        assert result.similarity == 1.0
        # The backend lookup was actually consulted with the right content/detail.
        mock_backend.find_active_by_content.assert_called_once_with(
            "exact duplicate summary", "exact duplicate detail"
        )

    def test_no_exact_match_falls_through_to_store_when_embeddings_disabled(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """No exact backend hit + embeddings off → store (embeddings gate honored)."""
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=False)

        mock_backend = MagicMock()
        mock_backend.find_active_by_content.return_value = None

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = check_duplicate(
                "novel summary",
                "novel detail",
                entries_dir,
                reader,
                config=config,
            )

        assert result.action == "store"
        assert result.existing_id is None

    def test_exact_match_short_circuits_before_embedding_path(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """An exact hit returns merge WITHOUT touching the embedding fast path.

        Even with embeddings enabled, the exact check wins first — embed() is
        never called because the exact merge returns before the gate.
        """
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        mock_backend = MagicMock()
        mock_backend.find_active_by_content.return_value = "L-exact-hit"

        embed_called: list[str] = []

        def tracking_embed(text: str) -> list[float]:
            embed_called.append(text)
            return [0.0] * 384

        with (
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend),
            patch("trw_mcp.state.dedup.embed", side_effect=tracking_embed),
        ):
            result = check_duplicate("s", "d", entries_dir, reader, config=config)

        assert result.action == "merge"
        assert result.existing_id == "L-exact-hit"
        # Exact path short-circuited before any embedding was computed.
        assert embed_called == []

    def test_backend_unavailable_fails_open_to_store(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """If the backend raises, the exact check fails open (store), never blocks."""
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=False)

        with patch(
            "trw_mcp.state.memory_adapter.get_backend",
            side_effect=RuntimeError("backend down"),
        ):
            result = check_duplicate("s", "d", entries_dir, reader, config=config)

        assert result.action == "store"
        assert result.existing_id is None
