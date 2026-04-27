"""Tests for MemoryStore — sqlite-vec vector store (PRD-CORE-041)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from trw_mcp.state.memory_store import MemoryStore

# sqlite-vec is an optional [vectors] extra — skip entire module when absent
_sqlite_vec = pytest.importorskip("sqlite_vec", reason="sqlite-vec not installed (optional [vectors] extra)")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, dim: int = 4) -> MemoryStore:
    from trw_mcp.state.memory_store import MemoryStore as _MemoryStore

    return _MemoryStore(tmp_path / "vectors.db", dim=dim)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestAvailability:
    def test_available_when_sqlite_vec_installed(self) -> None:
        from trw_mcp.state.memory_store import MemoryStore

        assert MemoryStore.available() is True

    def test_unavailable_when_sqlite_vec_missing(self) -> None:
        with patch.dict(sys.modules, {"sqlite_vec": None}):
            # Re-import to trigger fresh availability check
            import trw_mcp.state.memory_store as ms_mod

            # Directly test the function that uses the module-level flag
            # We patch the module-level flag
            original = ms_mod._SQLITE_VEC_AVAILABLE
            try:
                ms_mod._SQLITE_VEC_AVAILABLE = False
                from trw_mcp.state.memory_store import MemoryStore

                assert MemoryStore.available() is False
            finally:
                ms_mod._SQLITE_VEC_AVAILABLE = original


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "vectors.db"
        store = _make_store(tmp_path, dim=4)
        store.close()
        assert db_path.exists()

    def test_count_zero_on_fresh_store(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            assert store.count() == 0
        finally:
            store.close()

    def test_init_with_default_dim(self, tmp_path: Path) -> None:
        from trw_mcp.state.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "default.db")
        try:
            assert store.count() == 0
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Upsert + Search
# ---------------------------------------------------------------------------


class TestUpsertSearch:
    def test_upsert_then_search_returns_entry(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("entry-abc", [1.0, 0.0, 0.0, 0.0], {"summary": "test"})
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert len(results) == 1
            assert results[0][0] == "entry-abc"
        finally:
            store.close()

    def test_search_returns_entry_id_and_distance(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("entry-xyz", [0.5, 0.5, 0.0, 0.0], {"summary": "foo"})
            results = store.search([0.5, 0.5, 0.0, 0.0], top_k=1)
            assert len(results) == 1
            entry_id, distance = results[0]
            assert entry_id == "entry-xyz"
            assert isinstance(distance, float)
        finally:
            store.close()

    def test_search_returns_empty_when_no_entries(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert results == []
        finally:
            store.close()

    def test_search_top_k_limits_results(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            for i in range(5):
                store.upsert(f"entry-{i}", [float(i), 0.0, 0.0, 0.0], {})
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=2)
            assert len(results) <= 2
        finally:
            store.close()

    def test_upsert_twice_updates_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("entry-dup", [1.0, 0.0, 0.0, 0.0], {"summary": "first"})
            store.upsert("entry-dup", [0.0, 1.0, 0.0, 0.0], {"summary": "second"})
            assert store.count() == 1
        finally:
            store.close()

    def test_multiple_entries_ranked_by_distance(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("close", [1.0, 0.0, 0.0, 0.0], {})
            store.upsert("far", [0.0, 1.0, 0.0, 0.0], {})
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=10)
            ids = [r[0] for r in results]
            assert ids[0] == "close"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_removes_entry(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("del-me", [1.0, 0.0, 0.0, 0.0], {})
            assert store.count() == 1
            store.delete("del-me")
            assert store.count() == 0
        finally:
            store.close()

    def test_delete_nonexistent_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.delete("does-not-exist")  # should not raise
            assert store.count() == 0
        finally:
            store.close()

    def test_delete_only_removes_target(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("keep", [1.0, 0.0, 0.0, 0.0], {})
            store.upsert("remove", [0.0, 1.0, 0.0, 0.0], {})
            store.delete("remove")
            assert store.count() == 1
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert results[0][0] == "keep"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Graceful degradation when sqlite-vec unavailable
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_all_ops_noop_when_unavailable(self, tmp_path: Path) -> None:
        import trw_mcp.state.memory_store as ms_mod

        original = ms_mod._SQLITE_VEC_AVAILABLE
        try:
            ms_mod._SQLITE_VEC_AVAILABLE = False
            from trw_mcp.state.memory_store import MemoryStore

            store = MemoryStore(tmp_path / "noop.db", dim=4)
            # All ops should be no-ops or return empty/zero
            store.upsert("x", [1.0, 0.0, 0.0, 0.0], {})
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert results == []
            assert store.count() == 0
            store.delete("x")  # no error
            store.close()
        finally:
            ms_mod._SQLITE_VEC_AVAILABLE = original

    def test_available_returns_false_when_flag_false(self, tmp_path: Path) -> None:
        import trw_mcp.state.memory_store as ms_mod

        original = ms_mod._SQLITE_VEC_AVAILABLE
        try:
            ms_mod._SQLITE_VEC_AVAILABLE = False
            from trw_mcp.state.memory_store import MemoryStore

            assert MemoryStore.available() is False
        finally:
            ms_mod._SQLITE_VEC_AVAILABLE = original


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------


class TestCount:
    def test_count_increments_with_upsert(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        try:
            assert store.count() == 0
            store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})
            assert store.count() == 1
            store.upsert("b", [0.0, 1.0, 0.0, 0.0], {})
            assert store.count() == 2
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Search error path
# ---------------------------------------------------------------------------


class TestSearchErrorPath:
    def test_search_returns_empty_on_exception(self, tmp_path: Path) -> None:
        """When the database query raises, search returns [] without propagating.

        sqlite3.Connection.execute is read-only, so we replace the whole
        connection object with a MagicMock that raises on MATCH queries.
        """
        import sqlite3
        from unittest.mock import MagicMock

        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("entry-err", [1.0, 0.0, 0.0, 0.0], {})

            # Replace _conn with a mock that raises on execute when "MATCH" is in SQL
            real_conn = store._conn
            mock_conn = MagicMock(spec=sqlite3.Connection)

            def mock_execute(sql: str, params: object = ()) -> object:
                if "MATCH" in sql:
                    raise sqlite3.OperationalError("forced search error")
                return real_conn.execute(sql, params)  # type: ignore[arg-type]

            mock_conn.execute = mock_execute
            store._conn = mock_conn  # type: ignore[assignment]

            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert results == []
        finally:
            # Restore real connection before closing
            store._conn = real_conn  # type: ignore[assignment]
            store.close()


# ---------------------------------------------------------------------------
# Close idempotency
# ---------------------------------------------------------------------------


class TestCloseIdempotency:
    def test_close_twice_is_noop(self, tmp_path: Path) -> None:
        """Calling close() twice should not raise."""
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.close()  # Should not raise

    def test_count_after_close_returns_zero(self, tmp_path: Path) -> None:
        """After close, count() returns 0 (conn is None)."""
        store = _make_store(tmp_path, dim=4)
        store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})
        store.close()
        assert store.count() == 0

    def test_upsert_after_close_is_noop(self, tmp_path: Path) -> None:
        """After close, upsert is a no-op (conn is None)."""
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})  # Should not raise

    def test_search_after_close_returns_empty(self, tmp_path: Path) -> None:
        """After close, search returns []."""
        store = _make_store(tmp_path, dim=4)
        store.close()
        results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
        assert results == []

    def test_delete_after_close_is_noop(self, tmp_path: Path) -> None:
        """After close, delete is a no-op."""
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.delete("some-id")  # Should not raise


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


class TestMigrate:
    def test_migrate_returns_zero_when_conn_none(self, tmp_path: Path) -> None:
        """When conn is None (unavailable), migrate returns all zeros."""
        import trw_mcp.state.memory_store as ms_mod

        original = ms_mod._SQLITE_VEC_AVAILABLE
        try:
            ms_mod._SQLITE_VEC_AVAILABLE = False
            from trw_mcp.state.memory_store import MemoryStore
            from trw_mcp.state.persistence import FileStateReader

            store = MemoryStore(tmp_path / "noop.db", dim=4)
            entries_dir = tmp_path / "entries"
            entries_dir.mkdir()
            result = store.migrate(entries_dir, FileStateReader())
            assert result == {"migrated": 0, "skipped": 0, "total": 0}
        finally:
            ms_mod._SQLITE_VEC_AVAILABLE = original

    def test_migrate_returns_zero_when_embedding_unavailable(self, tmp_path: Path) -> None:
        """When embedding unavailable, migrate returns all zeros.

        embed_batch and embedding_available are local imports inside migrate(),
        so we patch them at the source module.
        """
        from trw_mcp.state.memory_store import MemoryStore
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = MemoryStore(tmp_path / "vectors.db", dim=4)
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
        """When entries_dir is empty (no YAML files), migrate returns all zeros."""
        from trw_mcp.state.memory_store import MemoryStore
        from trw_mcp.state.persistence import FileStateReader

        store = MemoryStore(tmp_path / "vectors.db", dim=4)
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
        """migrate() embeds active entries and upserts into the store."""
        from trw_mcp.state.memory_store import MemoryStore
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = MemoryStore(tmp_path / "vectors.db", dim=4)
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
        """migrate() skips resolved/obsolete entries."""
        from trw_mcp.state.memory_store import MemoryStore
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = MemoryStore(tmp_path / "vectors.db", dim=4)
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
            mock_batch.assert_not_called()  # No entries to embed
        finally:
            store.close()

    def test_migrate_counts_skipped_when_embed_returns_none(self, tmp_path: Path) -> None:
        """migrate() counts skipped entries when embed_batch returns None for some."""
        from trw_mcp.state.memory_store import MemoryStore
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        store = MemoryStore(tmp_path / "vectors.db", dim=4)
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
            # embed_text_batch returns [None] → 1 skipped, 0 migrated
            with patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True):
                with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[None]):
                    result = store.migrate(entries_dir, FileStateReader())
            assert result["migrated"] == 0
            assert result["skipped"] == 1
            assert result["total"] == 1
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Regression: macOS Python without SQLITE_ENABLE_LOAD_EXTENSION
# ---------------------------------------------------------------------------


class TestExtensionLoadFailureDegradesGracefully:
    """Regression: fresh macOS installs hit this when the system Python is
    compiled without SQLITE_ENABLE_LOAD_EXTENSION. enable_load_extension then
    raises AttributeError (method absent) or OperationalError (not authorized).

    Before the fix, MemoryStore.__init__ propagated the error, which surfaced
    as "sqlite extension error in the MCP server" at every trw_learn call and
    blocked learning persistence on fresh Mac installs.
    """

    @staticmethod
    def _install_bad_connect(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
        import sqlite3

        original_connect = sqlite3.connect

        class _ConnProxy:
            def __init__(self, conn: sqlite3.Connection, exc: Exception) -> None:
                self._conn = conn
                self._exc = exc

            def enable_load_extension(self, _enabled: bool) -> None:
                raise self._exc

            def __getattr__(self, name: str) -> object:
                return getattr(self._conn, name)

        def connect_proxy(*args: object, **kwargs: object) -> sqlite3.Connection:
            return _ConnProxy(original_connect(*args, **kwargs), exc)  # type: ignore[arg-type,return-value]

        monkeypatch.setattr(sqlite3, "connect", connect_proxy)

    def test_init_survives_attribute_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.memory_store import MemoryStore

        self._install_bad_connect(monkeypatch, AttributeError("enable_load_extension not available"))

        store = MemoryStore(tmp_path / "noext.db")

        assert store._conn is None
        assert store.count() == 0
        assert store.search([0.1] * 4, top_k=5) == []
        store.close()

    def test_init_survives_operational_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import sqlite3 as _sqlite3

        from trw_mcp.state.memory_store import MemoryStore

        self._install_bad_connect(monkeypatch, _sqlite3.OperationalError("not authorized"))

        store = MemoryStore(tmp_path / "opfail.db")

        assert store._conn is None
        store.close()
