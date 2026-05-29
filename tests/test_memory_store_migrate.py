"""Migration tests for MemoryStore — sqlite-vec vector store (PRD-CORE-041)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._test_memory_store_support import _make_store


class TestMigrate:
    def test_migrate_returns_zero_when_conn_none(self, tmp_path: Path) -> None:
        import trw_mcp.state.memory_store as ms_mod
        from trw_mcp.state.persistence import FileStateReader

        original = ms_mod._SQLITE_VEC_AVAILABLE
        try:
            ms_mod._SQLITE_VEC_AVAILABLE = False
            from trw_mcp.state.memory_store import MemoryStore

            store = MemoryStore(tmp_path / "noop.db", dim=4)
            entries_dir = tmp_path / "entries"
            entries_dir.mkdir()
            result = store.migrate(entries_dir, FileStateReader())
            assert result == {"migrated": 0, "skipped": 0, "total": 0}
        finally:
            ms_mod._SQLITE_VEC_AVAILABLE = original

    def test_migrate_returns_zero_when_embedding_unavailable(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = _make_store(tmp_path, dim=4)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            entries_dir / "L-test.yaml",
            {
                "id": "L-test",
                "summary": "test summary",
                "detail": "test detail",
                "status": "active",
            },
        )
        try:
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=False):
                result = store.migrate(entries_dir, FileStateReader())
            assert result == {"migrated": 0, "skipped": 0, "total": 0}
        finally:
            store.close()

    def test_migrate_returns_zero_when_no_entries(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader

        store = _make_store(tmp_path, dim=4)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        try:
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[]):
                    result = store.migrate(entries_dir, FileStateReader())
            assert result == {"migrated": 0, "skipped": 0, "total": 0}
        finally:
            store.close()

    def test_migrate_embeds_active_entries(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = _make_store(tmp_path, dim=4)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            entries_dir / "L-a.yaml",
            {
                "id": "L-a",
                "summary": "alpha summary",
                "detail": "alpha detail",
                "status": "active",
            },
        )
        writer.write_yaml(
            entries_dir / "L-b.yaml",
            {
                "id": "L-b",
                "summary": "beta summary",
                "detail": "beta detail",
                "status": "active",
            },
        )
        try:
            fake_embeds = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=fake_embeds):
                    result = store.migrate(entries_dir, FileStateReader())
            assert result["migrated"] == 2
            assert result["skipped"] == 0
            assert result["total"] == 2
            assert store.count() == 2
        finally:
            store.close()

    def test_migrate_skips_non_active_entries(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = _make_store(tmp_path, dim=4)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            entries_dir / "L-res.yaml",
            {
                "id": "L-res",
                "summary": "resolved entry",
                "detail": "resolved detail",
                "status": "resolved",
            },
        )
        try:
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[]) as mock_batch:
                    result = store.migrate(entries_dir, FileStateReader())
            assert result == {"migrated": 0, "skipped": 0, "total": 0}
            mock_batch.assert_not_called()
        finally:
            store.close()

    def test_migrate_counts_skipped_when_embed_returns_none(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = _make_store(tmp_path, dim=4)
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            entries_dir / "L-x.yaml",
            {
                "id": "L-x",
                "summary": "some summary",
                "detail": "some detail",
                "status": "active",
            },
        )
        try:
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[None]):
                    result = store.migrate(entries_dir, FileStateReader())
            assert result["migrated"] == 0
            assert result["skipped"] == 1
            assert result["total"] == 1
        finally:
            store.close()
