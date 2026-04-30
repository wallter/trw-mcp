"""Core tests for MemoryStore — sqlite-vec vector store (PRD-CORE-041)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from tests._test_memory_store_support import _make_store


class TestAvailability:
    def test_available_when_sqlite_vec_installed(self) -> None:
        from trw_mcp.state.memory_store import MemoryStore

        assert MemoryStore.available() is True

    def test_unavailable_when_sqlite_vec_missing(self) -> None:
        with patch.dict(sys.modules, {"sqlite_vec": None}):
            import trw_mcp.state.memory_store as ms_mod

            original = ms_mod._SQLITE_VEC_AVAILABLE
            try:
                ms_mod._SQLITE_VEC_AVAILABLE = False
                from trw_mcp.state.memory_store import MemoryStore

                assert MemoryStore.available() is False
            finally:
                ms_mod._SQLITE_VEC_AVAILABLE = original


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
            ids = [result[0] for result in results]
            assert ids[0] == "close"
        finally:
            store.close()


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
            store.delete("does-not-exist")
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


class TestDegradation:
    def test_all_ops_noop_when_unavailable(self, tmp_path: Path) -> None:
        import trw_mcp.state.memory_store as ms_mod

        original = ms_mod._SQLITE_VEC_AVAILABLE
        try:
            ms_mod._SQLITE_VEC_AVAILABLE = False
            from trw_mcp.state.memory_store import MemoryStore

            store = MemoryStore(tmp_path / "noop.db", dim=4)
            store.upsert("x", [1.0, 0.0, 0.0, 0.0], {})
            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert results == []
            assert store.count() == 0
            store.delete("x")
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
