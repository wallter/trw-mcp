"""Tests for dedup backend fast path integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import check_duplicate
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from tests._dedup_test_support import mock_embed, write_entry

class TestCheckDuplicateViaBackend:
    """Tests for the sqlite-vec fast path."""

    def test_backend_skip_against_active_entry(self, tmp_path: Path) -> None:
        """Backend returns 'skip' for active entry above skip threshold."""
        from unittest.mock import MagicMock

        from trw_mcp.state.dedup import _check_duplicate_via_backend

        mock_entry = MagicMock()
        mock_entry.status = MagicMock()
        mock_entry.status.value = "active"

        mock_backend = MagicMock()
        # distance=0 → similarity=1.0 (identical)
        mock_backend.search_vectors.return_value = [("L-active01", 0.0)]
        mock_backend.get.return_value = mock_entry

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is not None
        assert result.action == "skip"
        assert result.existing_id == "L-active01"
        assert result.similarity >= 0.95

    def test_backend_skip_against_obsolete_entry(self, tmp_path: Path) -> None:
        """Backend returns 'skip' for obsolete entry above skip threshold."""
        from unittest.mock import MagicMock

        from trw_mcp.state.dedup import _check_duplicate_via_backend

        mock_entry = MagicMock()
        mock_entry.status = MagicMock()
        mock_entry.status.value = "obsolete"

        mock_backend = MagicMock()
        mock_backend.search_vectors.return_value = [("L-obsolete01", 0.0)]
        mock_backend.get.return_value = mock_entry

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is not None
        assert result.action == "skip"
        assert result.existing_id == "L-obsolete01"

    def test_backend_no_merge_into_obsolete_entry(self, tmp_path: Path) -> None:
        """Backend returns 'store' for obsolete entry in merge zone (0.85-0.95)."""
        import math
        from unittest.mock import MagicMock

        from trw_mcp.state.dedup import _check_duplicate_via_backend

        mock_entry = MagicMock()
        mock_entry.status = MagicMock()
        mock_entry.status.value = "obsolete"

        mock_backend = MagicMock()
        # distance for similarity=0.90: d² = 2*(1-0.90) = 0.2, d = sqrt(0.2)
        distance_for_090 = math.sqrt(0.2)
        mock_backend.search_vectors.return_value = [("L-obsolete-merge", distance_for_090)]
        mock_backend.get.return_value = mock_entry

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is not None
        assert result.action == "store"

    def test_backend_merge_into_active_entry(self, tmp_path: Path) -> None:
        """Backend returns 'merge' for active entry in merge zone."""
        import math
        from unittest.mock import MagicMock

        from trw_mcp.state.dedup import _check_duplicate_via_backend

        mock_entry = MagicMock()
        mock_entry.status = MagicMock()
        mock_entry.status.value = "active"

        mock_backend = MagicMock()
        distance_for_090 = math.sqrt(0.2)
        mock_backend.search_vectors.return_value = [("L-active-merge", distance_for_090)]
        mock_backend.get.return_value = mock_entry

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is not None
        assert result.action == "merge"
        assert result.existing_id == "L-active-merge"

    def test_backend_returns_none_on_empty_results(self, tmp_path: Path) -> None:
        """Backend returns None when no vectors are indexed."""
        from unittest.mock import MagicMock

        from trw_mcp.state.dedup import _check_duplicate_via_backend

        mock_backend = MagicMock()
        mock_backend.search_vectors.return_value = []

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is None  # Signals caller to fall back to YAML scan

    def test_backend_returns_none_on_exception(self, tmp_path: Path) -> None:
        """Backend returns None when get_backend raises (triggers YAML fallback)."""
        from trw_mcp.state.dedup import _check_duplicate_via_backend

        with patch("trw_mcp.state.memory_adapter.get_backend", side_effect=RuntimeError("no db")):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is None

    def test_backend_skips_entries_not_in_db(self, tmp_path: Path) -> None:
        """Backend skips entries where backend.get() returns None."""
        from unittest.mock import MagicMock

        from trw_mcp.state.dedup import _check_duplicate_via_backend

        mock_backend = MagicMock()
        mock_backend.search_vectors.return_value = [("L-ghost", 0.0)]
        mock_backend.get.return_value = None  # Entry not found in memories table

        with patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend):
            result = _check_duplicate_via_backend([0.0] * 384, tmp_path, 0.95, 0.85)

        assert result is not None
        assert result.action == "store"

class TestCheckDuplicateFastPathIntegration:
    """Integration tests verifying check_duplicate uses the backend fast path."""

    def test_uses_backend_when_available(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """check_duplicate uses backend result and doesn't scan YAML files."""
        from trw_mcp.state.dedup import DedupResult as _DedupResult

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        # Write an active entry to YAML (should NOT be scanned if backend path works)
        write_entry(entries_dir, writer, "L-yaml-only", "yaml only summary", "yaml detail")

        backend_result = _DedupResult("skip", "L-from-backend", 0.99)

        with (
            patch("trw_mcp.state.dedup.embed", side_effect=mock_embed),
            patch("trw_mcp.state.dedup._check_duplicate_via_backend", return_value=backend_result),
        ):
            result = check_duplicate("any summary", "any detail", entries_dir, reader, config=config)

        assert result.action == "skip"
        assert result.existing_id == "L-from-backend"

    def test_falls_back_to_yaml_when_backend_unavailable(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """check_duplicate falls back to YAML scan when backend returns None."""
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        summary = "yaml fallback test summary"
        detail = "yaml fallback test detail"
        write_entry(entries_dir, writer, "L-yaml01", summary, detail)

        with (
            patch("trw_mcp.state.dedup.embed", side_effect=mock_embed),
            patch("trw_mcp.state.dedup._check_duplicate_via_backend", return_value=None),
        ):
            result = check_duplicate(summary, detail, entries_dir, reader, config=config)

        # YAML fallback finds the active entry → skip
        assert result.action == "skip"
        assert result.existing_id == "L-yaml01"
