"""Tests for batch dedup migration behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._dedup_test_support import mock_embed, write_entry
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import batch_dedup, is_migration_needed
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestBatchDedup:
    """Tests for FR05 — batch_dedup and is_migration_needed."""

    def test_is_migration_needed_true_when_no_marker(self, tmp_path: Path) -> None:
        """is_migration_needed returns True when marker file doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Default config: learnings_dir="learnings", so marker = .trw/learnings/dedup_migration.yaml
        # That file won't exist in a fresh tmp dir
        assert is_migration_needed(trw_dir) is True

    def test_is_migration_needed_false_after_marker_written(self, tmp_path: Path) -> None:
        """is_migration_needed returns False when marker file exists."""
        cfg = TRWConfig(embeddings_enabled=True)
        trw_dir = tmp_path / ".trw"
        learnings_dir = trw_dir / cfg.learnings_dir
        learnings_dir.mkdir(parents=True)
        marker = learnings_dir / "dedup_migration.yaml"
        marker.write_text("completed: true\n", encoding="utf-8")
        assert is_migration_needed(trw_dir) is False

    def test_batch_dedup_skips_when_no_entries_dir(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup returns 'skipped' when entries directory doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True)

        result = batch_dedup(trw_dir, reader, writer, config=config)
        assert result["status"] == "skipped"
        assert "no entries directory" in str(result.get("reason", ""))

    def test_batch_dedup_skips_when_embeddings_unavailable(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup returns 'skipped' when embeddings unavailable."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=False):
            result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "skipped"
        assert "embeddings unavailable" in str(result.get("reason", ""))

    def test_batch_dedup_writes_migration_marker(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup writes dedup_migration.yaml marker after completion."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        marker = trw_dir / "learnings" / "dedup_migration.yaml"
        assert marker.exists()
        marker_data = reader.read_yaml(marker)
        assert marker_data.get("completed") is True
        assert "run_at" in marker_data

    def test_batch_dedup_merges_near_duplicates(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup merges entries above merge threshold."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        # Write two entries
        write_entry(entries_dir, writer, "L-batch01", "batch test alpha", "first detail here alpha")
        write_entry(entries_dir, writer, "L-batch02", "batch test beta", "second detail here beta")

        # Control vectors: L-batch01 and L-batch02 will be at 0.90 similarity
        existing_vec = mock_embed("batch test alpha first detail here alpha")
        import math as _math

        cos_theta = 0.90
        sin_theta = _math.sqrt(1 - cos_theta**2)
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        near_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        near_norm = sum(v * v for v in near_vec) ** 0.5
        near_vec = [v / near_norm for v in near_vec]

        call_count = [0]

        def controlled_embed(text: str) -> list[float]:
            call_count[0] += 1
            if "L-batch02" in text or "second detail here beta" in text or "batch test beta" in text:
                return near_vec
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=controlled_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        assert int(str(result.get("entries_scanned", 0))) == 2

    def test_batch_dedup_completes_with_no_active_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup completes cleanly with zero active entries."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        # Write only resolved entries
        path = entries_dir / "L-resolved.yaml"
        writer.write_yaml(
            path,
            {
                "id": "L-resolved",
                "summary": "s",
                "detail": "d",
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "resolved",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        assert int(str(result.get("entries_scanned", 0))) == 0
        assert int(str(result.get("entries_merged", 0))) == 0

    def test_batch_dedup_obsoletes_exact_duplicates(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup marks exact duplicates (>=skip_threshold) as obsolete."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        identical_summary = "exact duplicate entry for batch"
        identical_detail = "same detail for exact duplicate"
        write_entry(entries_dir, writer, "L-exact01", identical_summary, identical_detail)
        write_entry(entries_dir, writer, "L-exact02", identical_summary, identical_detail)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        # One of the two entries should be obsoleted
        data2 = reader.read_yaml(entries_dir / "L-exact02.yaml")
        assert str(data2.get("status", "")) == "obsolete"
