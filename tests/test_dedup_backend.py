"""dedup_verdict takes the store's dense verdict when it has one and falls back to the YAML scan otherwise.

The dense verdict itself runs through the real daemon in test_dedup_daemon_parity.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._dedup_test_support import mock_embed, write_entry
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import dedup_verdict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestCheckDuplicateFastPathIntegration:
    """Integration tests verifying dedup_verdict uses the backend fast path."""

    def test_uses_backend_when_available(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """dedup_verdict uses backend result and doesn't scan YAML files."""
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
            result = dedup_verdict("any summary", "any detail", entries_dir, reader, config=config)

        assert result.action == "skip"
        assert result.existing_id == "L-from-backend"

    def test_falls_back_to_yaml_when_backend_unavailable(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """dedup_verdict falls back to YAML scan when backend returns None."""
        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        summary = "yaml fallback test summary"
        detail = "yaml fallback test detail"
        write_entry(entries_dir, writer, "L-yaml01", summary, detail)

        with (
            patch("trw_mcp.state.dedup.embed", side_effect=mock_embed),
            patch("trw_mcp.state.dedup._check_duplicate_via_backend", return_value=None),
            # Isolate the YAML-fallback embedding path: suppress the
            # embedding-independent exact-content check (covered separately).
            patch("trw_mcp.state.dedup._check_exact_content_duplicate", return_value=None),
        ):
            result = dedup_verdict(summary, detail, entries_dir, reader, config=config)

        # YAML fallback finds the active entry → skip
        assert result.action == "skip"
        assert result.existing_id == "L-yaml01"
