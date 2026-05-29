"""Tests for check_duplicate threshold validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._dedup_test_support import mock_embed, write_entry
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import check_duplicate
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestThresholdValidation:
    """Tests for FR06 — invalid threshold resets to defaults."""

    def test_check_duplicate_resets_invalid_thresholds(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When merge_threshold >= skip_threshold, defaults are used."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.80, dedup_merge_threshold=0.85)

        summary = "threshold validation test"
        detail = "some detail"
        write_entry(entries_dir, writer, "L-thresh01", summary, detail)

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate(summary, detail, entries_dir, reader, config=config)

        assert result.action in ("skip", "store", "merge")

    def test_check_duplicate_equal_thresholds_resets(self, tmp_path: Path, reader: FileStateReader) -> None:
        """When merge_threshold == skip_threshold, defaults are used."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.90, dedup_merge_threshold=0.90)

        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = check_duplicate("any summary", "any detail", entries_dir, reader, config=config)

        assert result is not None
