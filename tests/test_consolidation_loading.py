"""SQLite and YAML loading tests for consolidation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.consolidation import _load_active_entries, find_clusters
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

from ._consolidation_test_helpers import make_vec, write_entry

class TestFindClustersSQLite:
    """PRD-FIX-033-FR04: find_clusters loads entries from SQLite when available."""

    def test_find_clusters_uses_sqlite(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """find_clusters calls list_active_learnings instead of glob when available."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Pre-built entries that would come from SQLite
        fake_entries: list[dict[str, object]] = [
            {
                "id": f"L-sql{i:02d}",
                "summary": f"similar topic about testing {i}",
                "detail": f"detail {i}",
                "status": "active",
                "impact": 0.5,
                "tags": ["testing"],
                "source_type": "agent",
            }
            for i in range(5)
        ]

        with (
            patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                return_value=fake_entries,
            ) as mock_sqlite,
            patch(
                "trw_mcp.state.memory_adapter.embedding_available",
                return_value=True,
            ),
            patch(
                "trw_mcp.state.memory_adapter.embed_text_batch",
                return_value=[make_vec(1.0, 0.0)] * 5,
            ),
        ):
            result = find_clusters(
                entries_dir,
                reader,
                similarity_threshold=0.9,
                min_cluster_size=3,
            )

        mock_sqlite.assert_called_once()
        # All 5 entries have identical vectors → 1 cluster of 5
        assert len(result) >= 1

    def test_find_clusters_fallback_to_yaml(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Falls back to YAML glob when SQLite raises."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Write YAML entries for fallback
        for i in range(4):
            write_entry(
                entries_dir,
                writer,
                f"L-fb{i:02d}",
                summary=f"yaml fallback testing {i}",
            )

        with (
            patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                side_effect=RuntimeError("SQLite unavailable"),
            ),
            patch(
                "trw_mcp.state.memory_adapter.embedding_available",
                return_value=True,
            ),
            patch(
                "trw_mcp.state.memory_adapter.embed_text_batch",
                return_value=[make_vec(1.0, 0.0)] * 4,
            ),
        ):
            result = find_clusters(
                entries_dir,
                reader,
                similarity_threshold=0.9,
                min_cluster_size=3,
            )

        # YAML fallback should still load entries and find clusters
        assert len(result) >= 1

    def test_find_clusters_sqlite_filters_consolidated(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """SQLite path filters out consolidated and archived entries."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {
                "id": "L-active1",
                "summary": "test",
                "detail": "d",
                "status": "active",
                "impact": 0.5,
                "tags": [],
                "source_type": "agent",
            },
            {
                "id": "L-consolidated",
                "summary": "test",
                "detail": "d",
                "status": "active",
                "impact": 0.5,
                "tags": [],
                "source_type": "consolidated",
            },
            {
                "id": "L-archived",
                "summary": "test",
                "detail": "d",
                "status": "active",
                "impact": 0.5,
                "tags": [],
                "source_type": "agent",
                "consolidated_into": "L-xyz",
            },
        ]

        with (
            patch(
                "trw_mcp.state.memory_adapter.list_active_learnings",
                return_value=fake_entries,
            ),
            patch(
                "trw_mcp.state.memory_adapter.embedding_available",
                return_value=True,
            ),
            patch(
                "trw_mcp.state.memory_adapter.embed_text_batch",
                return_value=[make_vec(1.0, 0.0)],  # Only 1 entry passes filters
            ),
        ):
            result = find_clusters(
                entries_dir,
                reader,
                similarity_threshold=0.5,
                min_cluster_size=2,
            )

        # Only 1 entry passes filters (< min_cluster_size=2), so no clusters
        assert result == []

class TestLoadActiveEntries:
    """Direct unit tests for _load_active_entries."""

    def test_sqlite_path_returns_entries(self, tmp_path: Path, reader: FileStateReader) -> None:
        """When SQLite succeeds, returns entries from list_active_learnings."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {"id": f"e{i}", "summary": f"s{i}", "status": "active"} for i in range(4)
        ]
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=fake_entries,
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert len(result) == 4

    def test_sqlite_path_respects_max_entries(self, tmp_path: Path, reader: FileStateReader) -> None:
        """SQLite path caps entries at max_entries."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {"id": f"e{i}", "summary": f"s{i}", "status": "active"} for i in range(10)
        ]
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=fake_entries,
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=3)

        assert len(result) == 3

    def test_sqlite_path_filters_consolidated(self, tmp_path: Path, reader: FileStateReader) -> None:
        """SQLite path filters out consolidated entries via _is_clusterable."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {"id": "e1", "summary": "s1", "source_type": "consolidated"},
            {"id": "e2", "summary": "s2", "consolidated_into": "L-abc"},
            {"id": "e3", "summary": "s3", "status": "active"},
        ]
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=fake_entries,
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert len(result) == 1
        assert result[0]["id"] == "e3"

    def test_yaml_fallback_when_sqlite_raises(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """When SQLite raises, falls back to YAML glob and loads active entries."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001", status="active")
        write_entry(entries_dir, writer, "e002", status="active")
        write_entry(entries_dir, writer, "e003", status="archived")

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=ImportError("no adapter"),
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert len(result) == 2

    def test_yaml_fallback_respects_max_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """YAML fallback caps at max_entries."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        for i in range(10):
            write_entry(entries_dir, writer, f"e{i:03d}", status="active")

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("no sqlite"),
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=3)

        assert len(result) == 3

    def test_yaml_fallback_skips_index_yaml(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """YAML fallback skips index.yaml file."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer.write_yaml(entries_dir / "index.yaml", {"version": 1})
        write_entry(entries_dir, writer, "e001", status="active")

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("no sqlite"),
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert len(result) == 1
        assert result[0]["id"] == "e001"

    def test_yaml_fallback_skips_unreadable_files(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """YAML fallback skips files that cannot be parsed."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        (entries_dir / "bad.yaml").write_text("{{invalid")
        write_entry(entries_dir, writer, "e001", status="active")

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("no sqlite"),
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert len(result) == 1

    def test_yaml_fallback_filters_consolidated_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """YAML fallback filters out consolidated source_type entries."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        write_entry(entries_dir, writer, "e001", status="active", source_type="consolidated")
        write_entry(entries_dir, writer, "e002", status="active")

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            side_effect=RuntimeError("no sqlite"),
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert len(result) == 1
        assert result[0]["id"] == "e002"

    def test_sqlite_returns_empty_for_all_consolidated(self, tmp_path: Path, reader: FileStateReader) -> None:
        """When all SQLite entries are consolidated, returns empty list."""
        entries_dir = tmp_path / ".trw" / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        fake_entries: list[dict[str, object]] = [
            {"id": "e1", "source_type": "consolidated"},
            {"id": "e2", "consolidated_into": "L-xxx"},
        ]
        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=fake_entries,
        ):
            result = _load_active_entries(entries_dir, reader, max_entries=10)

        assert result == []
