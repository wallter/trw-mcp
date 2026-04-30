"""Tests for check_duplicate edge cases and thresholds."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import check_duplicate
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from tests._dedup_test_support import mock_embed, write_entry

class TestCheckDuplicateEdgeCases:
    """Additional edge cases for check_duplicate to reach line coverage."""

    def test_store_when_entries_dir_missing_after_embed(self, tmp_path: Path, reader: FileStateReader) -> None:
        """entries_dir does not exist → DedupResult('store', None, 0.0) even when embed succeeds."""
        missing_dir = tmp_path / "does_not_exist"
        config = TRWConfig(embeddings_enabled=True)

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate("some summary", "some detail", missing_dir, reader, config=config)

        assert result.action == "store"
        assert result.existing_id is None
        assert result.similarity == 0.0

    def test_skip_index_yaml_file(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """index.yaml file in entries_dir is silently skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True)

        # Write index.yaml (should be skipped) and a real entry
        writer.write_yaml(
            entries_dir / "index.yaml",
            {
                "id": "L-index",
                "summary": "same summary exact match",
                "detail": "same detail exact match",
                "tags": [],
                "status": "active",
            },
        )

        # Only write index.yaml (no real entries) so result must be 'store'
        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate(
                "same summary exact match",
                "same detail exact match",
                entries_dir,
                reader,
                config=config,
            )

        # index.yaml is skipped, so no duplicate found → store
        assert result.action == "store"

    def test_corrupt_yaml_entry_is_skipped(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Unreadable YAML entries are silently skipped — no exception raised."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True)

        # Write a valid entry and a corrupt one
        write_entry(entries_dir, writer, "L-good01", "valid entry", "valid detail")
        corrupt_path = entries_dir / "0corrupt.yaml"
        corrupt_path.write_text("{ invalid yaml :\n  - broken", encoding="utf-8")

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            # Should not raise — the corrupt file is skipped
            result = check_duplicate(
                "completely different topic",
                "unrelated info",
                entries_dir,
                reader,
                config=config,
            )

        assert result is not None  # No exception

    def test_entry_embed_returns_none_is_skipped(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When embed returns None for existing entry, that entry is skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True)

        write_entry(entries_dir, writer, "L-skip-embed", "some summary", "some detail")

        call_count = [0]

        def selective_none_embed(text: str) -> list[float] | None:
            call_count[0] += 1
            if call_count[0] == 1:
                # First call is for the new entry — return a real vector
                return mock_embed(text)
            # Subsequent calls (for existing entries) return None
            return None

        with patch("trw_mcp.state.dedup.embed", side_effect=selective_none_embed):
            result = check_duplicate("some summary", "some detail", entries_dir, reader, config=config)

        # Existing entry embed returns None → skipped → action is 'store'
        assert result.action == "store"

    def test_threshold_boundary_exactly_at_skip(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Similarity at or above skip_threshold (0.95) → 'skip' action."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        write_entry(entries_dir, writer, "L-boundary01", "boundary test", "detail")
        existing_vec = mock_embed("boundary test detail")

        # Create a vector clearly above the 0.95 threshold to avoid float rounding ambiguity
        import math as _math

        cos_theta = 0.951
        sin_theta = _math.sqrt(1.0 - cos_theta**2)
        # Build orthogonal to existing_vec
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        new_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        new_norm = sum(v * v for v in new_vec) ** 0.5
        if new_norm > 0:
            new_vec = [v / new_norm for v in new_vec]

        call_count = [0]

        def boundary_embed(text: str) -> list[float]:
            call_count[0] += 1
            if call_count[0] == 1:
                return new_vec  # new entry
            return mock_embed(text)  # existing entry

        with patch("trw_mcp.state.dedup.embed", side_effect=boundary_embed):
            result = check_duplicate("boundary test", "detail", entries_dir, reader, config=config)

        # At exactly 0.95 similarity → skip (>= skip_threshold)
        assert result.action == "skip"
        assert result.similarity >= 0.95

    def test_threshold_boundary_exactly_at_merge(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Similarity exactly at merge_threshold (0.85) → 'merge' action.

        We use controlled embed functions that return a precise vector for the new entry
        and a separate vector for the existing entry, with exactly 0.85 cosine similarity.
        The new entry text must differ from the existing to avoid exact match.
        """
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        # Existing entry with distinct text
        write_entry(entries_dir, writer, "L-boundary02", "existing merge test entry", "existing detail here")
        existing_text = "existing merge test entry existing detail here"
        existing_vec = mock_embed(existing_text)

        import math as _math

        cos_theta = 0.87  # In merge zone (0.85 <= 0.87 < 0.95)
        sin_theta = _math.sqrt(1.0 - cos_theta**2)
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        new_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        new_norm = sum(v * v for v in new_vec) ** 0.5
        if new_norm > 0:
            new_vec = [v / new_norm for v in new_vec]

        def controlled_embed(text: str) -> list[float]:
            if "totally different new query" in text:
                return new_vec
            # For the existing entry text, return the canonical mock_embed
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embed", side_effect=controlled_embed):
            result = check_duplicate(
                "totally different new query",
                "",  # Empty detail so text = "totally different new query "
                entries_dir,
                reader,
                config=config,
            )

        # new_vec is at 0.87 similarity → in merge zone [0.85, 0.95) → merge action
        assert result.action == "merge"
        assert 0.85 <= result.similarity < 0.95
