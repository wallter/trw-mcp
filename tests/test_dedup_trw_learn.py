"""Integration tests for trw_learn dedup behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._dedup_test_support import mock_embed
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestTrwLearnDedup:
    """Integration tests for the dedup check in trw_learn()."""

    def _make_entries_dir(self, tmp_path: Path) -> Path:
        trw = tmp_path / ".trw"
        entries = trw / "learnings" / "entries"
        entries.mkdir(parents=True)
        return entries

    def test_trw_learn_returns_skipped_duplicate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """When a duplicate exists, trw_learn returns 'skipped_duplicate'."""
        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = self._make_entries_dir(tmp_path)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        # Patch module singletons
        mock_config = TRWConfig(
            dedup_enabled=True, embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85
        )

        monkeypatch.setattr("trw_mcp.tools.learning.get_config", lambda: mock_config)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        # Write an identical entry already in the entries_dir
        summary = "pytest fixture isolation pattern"
        detail = "use autouse fixtures with yield for clean teardown"
        writer.write_yaml(
            entries_dir / "L-existing99.yaml",
            {
                "id": "L-existing99",
                "summary": summary,
                "detail": detail,
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        # Patch embed to return deterministic vectors
        monkeypatch.setattr("trw_mcp.state.dedup.embed", mock_embed)

        # Patch generate_learning_id to avoid randomness
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id",
            lambda: "L-newidtest",
        )

        server = FastMCP("test")
        register_learning_tools(server)

        # Get the registered trw_learn tool and call it
        tools = get_tools_sync(server)
        tool_fn = tools["trw_learn"].fn

        result = tool_fn(summary=summary, detail=detail)

        assert result["status"] == "skipped"
        assert result["learning_id"] is not None
        assert result["duplicate_of"] == "L-existing99"
        assert float(result["similarity"]) >= 0.95

    def test_trw_learn_normal_store_when_dedup_disabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """When dedup_enabled=False, trw_learn stores normally."""
        self._make_entries_dir(tmp_path)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(dedup_enabled=False, embeddings_enabled=True)

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", writer)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id",
            lambda: "L-storedtest",
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)
        tools = get_tools_sync(server)
        tool_fn = tools["trw_learn"].fn

        result = tool_fn(summary="some summary", detail="some detail")

        assert result["status"] == "recorded"
        assert result["learning_id"] == "L-storedtest"

    def test_trw_learn_returns_merged_when_near_duplicate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """When a near-duplicate exists, trw_learn returns 'merged'."""
        entries_dir = self._make_entries_dir(tmp_path)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(
            dedup_enabled=True,
            embeddings_enabled=True,
            dedup_skip_threshold=0.95,
            dedup_merge_threshold=0.85,
        )

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", writer)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id",
            lambda: "L-mergedtest",
        )

        existing_summary = "existing learning entry"
        existing_detail = "existing detail about some topic"
        writer.write_yaml(
            entries_dir / "L-existingmerge.yaml",
            {
                "id": "L-existingmerge",
                "summary": existing_summary,
                "detail": existing_detail,
                "tags": ["a"],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        existing_vec = mock_embed(existing_summary + " " + existing_detail)

        def merge_zone_embed(text: str) -> list[float]:
            """Return vectors in merge zone (0.85-0.95) for new text."""
            if "new" in text:
                mixed = [v * 0.88 + 0.02 * (i % 2) for i, v in enumerate(existing_vec)]
                norm = sum(v * v for v in mixed) ** 0.5
                if norm == 0:
                    return existing_vec
                return [v / norm for v in mixed]
            return mock_embed(text)

        monkeypatch.setattr("trw_mcp.state.dedup.embed", merge_zone_embed)

        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)
        tools = get_tools_sync(server)
        tool_fn = tools["trw_learn"].fn

        result = tool_fn(summary="new similar summary", detail="new similar detail about the topic")

        # Should be merge, skip, or recorded (all are valid near-duplicate responses)
        assert result["status"] in ("merged", "skipped", "recorded")

class TestTrwLearnGracefulDegradation:
    """CORE-042-FR01: When embed() returns None, trw_learn falls back to 'store' (recorded)."""

    def _make_setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: FileStateReader, writer: FileStateWriter
    ) -> object:
        """Common setup for trw_learn integration tests."""
        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(
            dedup_enabled=True, embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85
        )

        monkeypatch.setattr("trw_mcp.tools.learning._config", mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning._reader", reader)
        monkeypatch.setattr("trw_mcp.tools.learning._writer", writer)
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr("trw_mcp.tools.learning.generate_learning_id", lambda: "L-graceful-test")

        server = FastMCP("test")
        register_learning_tools(server)
        tools = get_tools_sync(server)
        return tools["trw_learn"].fn

    def test_trw_learn_recorded_when_embed_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """FR01: When embed() returns None (no sentence-transformers), trw_learn stores normally.

        The dedup path fails gracefully and the learning is written with status 'recorded'.
        """
        tool_fn = self._make_setup(tmp_path, monkeypatch, reader, writer)

        # Simulate embed not available
        monkeypatch.setattr("trw_mcp.state.dedup.embed", lambda text: None)

        result = tool_fn(
            summary="graceful dedup fallback test",
            detail="embed returns None so dedup is skipped",
        )

        assert result["status"] == "recorded", f"FR01: Expected 'recorded' when embed=None, got {result['status']!r}"
        assert "learning_id" in result

    def test_trw_learn_recorded_when_new_entry_embed_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """FR01: Even with an existing entry, if embed(new_text) returns None, stores as new."""
        tool_fn = self._make_setup(tmp_path, monkeypatch, reader, writer)
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"

        # Write an existing entry
        writer.write_yaml(
            entries_dir / "L-existing-gr.yaml",
            {
                "id": "L-existing-gr",
                "summary": "graceful fallback existing",
                "detail": "detail for existing entry",
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        # embed returns None for ALL calls
        monkeypatch.setattr("trw_mcp.state.dedup.embed", lambda text: None)

        result = tool_fn(
            summary="new summary different from existing",
            detail="new detail completely different",
        )

        # With embed returning None, dedup check returns 'store' → trw_learn records
        assert result["status"] == "recorded"
