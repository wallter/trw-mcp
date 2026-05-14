"""Lifecycle tests for MemoryStore — sqlite-vec vector store (PRD-CORE-041)."""

from __future__ import annotations

from pathlib import Path

from tests._test_memory_store_support import _make_store


class TestSearchErrorPath:
    def test_search_returns_empty_on_exception(self, tmp_path: Path) -> None:
        import sqlite3
        from unittest.mock import MagicMock

        store = _make_store(tmp_path, dim=4)
        try:
            store.upsert("entry-err", [1.0, 0.0, 0.0, 0.0], {})

            real_conn = store._conn
            mock_conn = MagicMock(spec=sqlite3.Connection)

            def mock_execute(sql: str, params: object = ()) -> object:
                if "MATCH" in sql:
                    raise sqlite3.OperationalError("forced search error")
                return real_conn.execute(sql, params)  # type: ignore[arg-type]

            mock_conn.execute = mock_execute
            store._conn = mock_conn

            results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
            assert results == []
        finally:
            store._conn = real_conn
            store.close()


class TestCloseIdempotency:
    def test_close_twice_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.close()

    def test_count_after_close_returns_zero(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})
        store.close()
        assert store.count() == 0

    def test_upsert_after_close_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})

    def test_search_after_close_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        results = store.search([1.0, 0.0, 0.0, 0.0], top_k=5)
        assert results == []

    def test_delete_after_close_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.delete("some-id")
