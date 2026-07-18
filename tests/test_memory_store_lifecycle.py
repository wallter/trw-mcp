"""MemoryStore registry lifecycle regressions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests._test_memory_store_support import _make_store
from trw_mcp.state.memory_store import get_memory_store, reset_memory_store


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
                return real_conn.execute(sql, params)  # type: ignore[arg-type,union-attr]

            mock_conn.execute = mock_execute
            store._conn = mock_conn
            assert store.search([1.0, 0.0, 0.0, 0.0], top_k=5) == []
        finally:
            store._conn = real_conn
            store.close()


class TestCloseIdempotency:
    def test_close_twice_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.close()
        assert store.connected is False

    def test_count_after_close_returns_zero(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})
        store.close()
        assert store.count() == 0

    def test_upsert_after_close_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.upsert("a", [1.0, 0.0, 0.0, 0.0], {})
        assert store.count() == 0

    def test_search_after_close_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        assert store.search([1.0, 0.0, 0.0, 0.0], top_k=5) == []

    def test_delete_after_close_is_noop(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, dim=4)
        store.close()
        store.delete("some-id")
        assert store.count() == 0


def test_distinct_paths_do_not_invalidate_existing_store(tmp_path: Path) -> None:
    reset_memory_store()
    store_a = get_memory_store(tmp_path / "a.db")
    if not store_a.connected:
        return

    get_memory_store(tmp_path / "b.db")
    store_a.upsert("retained", [0.0] * 384, {})

    assert store_a.connected
    assert store_a.count() == 1
    reset_memory_store()


def test_path_scoped_reset_does_not_close_siblings(tmp_path: Path) -> None:
    reset_memory_store()
    path_a = tmp_path / "a.db"
    path_b = tmp_path / "b.db"
    store_a = get_memory_store(path_a)
    store_b = get_memory_store(path_b)

    reset_memory_store(path_a)

    assert not store_a.connected
    assert store_b.connected == get_memory_store(path_b).connected
    reset_memory_store()


def test_shared_store_serializes_concurrent_upserts(tmp_path: Path) -> None:
    reset_memory_store()
    store = get_memory_store(tmp_path / "shared.db")
    if not store.connected:
        reset_memory_store()
        return

    def write(index: int) -> None:
        store.upsert(f"entry-{index}", [float(index)] + [0.0] * 383, {})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(32)))

    assert store.count() == 32
    reset_memory_store()
