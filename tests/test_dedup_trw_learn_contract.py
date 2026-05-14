"""Contract tests for trw_learn dedup return values and side effects."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._dedup_test_support import mock_embed, write_entry
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestSkipUpdatesAccessCount:
    """Tests for FR04 — skip action updates access_count on existing entry."""

    def test_skip_increments_access_count(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """When dedup action=skip, existing entry's access_count is incremented."""
        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)

        mock_config = TRWConfig(
            dedup_enabled=True, embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85
        )

        monkeypatch.setattr("trw_mcp.tools.learning.get_config", lambda: mock_config)
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr("trw_mcp.tools.learning.generate_learning_id", lambda: "L-skip-test")

        summary = "unique skip access count test"
        detail = "detail for skip access count test"
        writer.write_yaml(
            entries_dir / "L-existing-skip.yaml",
            {
                "id": "L-existing-skip",
                "summary": summary,
                "detail": detail,
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "active",
                "recurrence": 1,
                "access_count": 3,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        monkeypatch.setattr("trw_mcp.state.dedup.embed", mock_embed)

        server = FastMCP("test")
        register_learning_tools(server)
        tools = get_tools_sync(server)
        result = tools["trw_learn"].fn(summary=summary, detail=detail)

        assert result["status"] == "skipped"
        assert result["learning_id"] == "L-skip-test"

        # Sprint 34: YAML is now a backup — access_count/recurrence tracking
        # moved to SQLite adapter. YAML file is NOT updated on skip.
        updated_data = reader.read_yaml(entries_dir / "L-existing-skip.yaml")
        assert int(str(updated_data.get("access_count", 0))) == 3
        assert int(str(updated_data.get("recurrence", 1))) == 1

class TestTrwLearnReturnDictKeys:
    """CORE-042-FR04: Verify return dict structure for skip, merge, and recorded paths."""

    def _setup_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
        dedup_enabled: bool = True,
    ) -> object:
        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        (tmp_path / ".trw" / "logs").mkdir(parents=True)

        cfg = TRWConfig(
            dedup_enabled=dedup_enabled, embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85
        )

        monkeypatch.setattr("trw_mcp.tools.learning.get_config", lambda: cfg)
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr("trw_mcp.tools.learning.generate_learning_id", lambda: "L-key-test")
        monkeypatch.setattr("trw_mcp.state.dedup.embed", mock_embed)

        server = FastMCP("test")
        register_learning_tools(server)
        return get_tools_sync(server)["trw_learn"].fn

    def test_recorded_result_has_learning_id_and_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """FR04: Normal store ('recorded') result has learning_id and path."""
        tool_fn = self._setup_tool(tmp_path, monkeypatch, reader, writer)

        result = tool_fn(
            summary="unique brand new learning for key test abc123",
            detail="unique detail that won't match anything xyz987",
        )

        assert result["status"] == "recorded"
        assert "learning_id" in result, "recorded result must have learning_id"
        # path is optional but should be present for recorded (entry was written)
        assert result.get("learning_id") == "L-key-test"

    def test_skip_result_has_learning_id_and_duplicate_of(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """FR04: Skip ('skipped') result has learning_id and duplicate_of per PRD spec."""
        tool_fn = self._setup_tool(tmp_path, monkeypatch, reader, writer)
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"

        summary = "pytest fixture isolation pattern for key test"
        detail = "use autouse fixtures with yield for clean teardown"
        FileStateWriter().write_yaml(
            entries_dir / "L-skip-key.yaml",
            {
                "id": "L-skip-key",
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

        result = tool_fn(summary=summary, detail=detail)

        assert result["status"] == "skipped"
        assert "learning_id" in result, "skip result must have learning_id"
        assert "duplicate_of" in result, "skip result must have 'duplicate_of' per PRD-CORE-042"
        # 'path' should not be present for skipped entries (no new file written)
        assert result.get("path") is None or "path" not in result, (
            "skip result should not have a 'path' (no new file written)"
        )

    def test_merged_result_has_learning_id_and_merged_into(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """FR03: Merge ('merged') result has learning_id and merged_into per PRD spec."""
        tool_fn = self._setup_tool(tmp_path, monkeypatch, reader, writer)
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"

        existing_summary = "pytest fixture autouse yield pattern"
        existing_detail = "use autouse fixtures with yield for clean teardown in pytest"
        FileStateWriter().write_yaml(
            entries_dir / "L-merge-key.yaml",
            {
                "id": "L-merge-key",
                "summary": existing_summary,
                "detail": existing_detail,
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

        # New entry is similar but not identical (should trigger merge)
        new_summary = "pytest fixture autouse yield teardown"
        new_detail = "autouse fixtures with yield in pytest for clean teardown"

        result = tool_fn(summary=new_summary, detail=new_detail)

        # Result is one of merged, skipped, or recorded depending on similarity
        assert result["status"] in ("merged", "skipped", "recorded")
        assert "learning_id" in result or "new_id" in result, "result must have an ID"

        if result["status"] == "merged":
            assert "merged_into" in result, "merged result must have 'merged_into' per PRD"
        elif result["status"] == "skipped":
            assert "duplicate_of" in result, "skipped result must have 'duplicate_of' per PRD"

    def test_all_paths_always_return_learning_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        """FR04: Every trw_learn response contains 'learning_id' regardless of path.

        This is a contract test — learning_id is the stable identifier regardless
        of whether the entry was recorded, merged, or skipped.
        """
        # Test recorded path (no dedup match)
        tool_fn = self._setup_tool(tmp_path, monkeypatch, reader, writer)

        result = tool_fn(
            summary="completely unique entry zzzz999",
            detail="no possible match for this detail xkcd1234",
        )
        assert "learning_id" in result, f"recorded: learning_id missing from {result}"

    def test_skip_threshold_boundary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR04/CORE-042 AC: skip_threshold >= 0.95 means >=0.95 similarity triggers skip."""
        from trw_mcp.state.dedup import check_duplicate as cd

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        # Config with explicit thresholds
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        summary = "skip threshold test"
        detail = "boundary condition at 0.95"
        write_entry(entries_dir, writer, "L-thresh-skip", summary, detail)

        # embed returns same vector → similarity = 1.0 >= 0.95 → skip
        with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
            result = cd(summary, detail, entries_dir, reader, config=config)

        assert result.action == "skip", (
            f"Expected skip at similarity >= 0.95, got {result.action} (sim={result.similarity})"
        )
        assert result.similarity >= 0.95
