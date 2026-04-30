"""Regression tests for MemoryStore — sqlite-vec vector store (PRD-CORE-041)."""

from __future__ import annotations

import pytest

from tests._test_memory_store_support import _sqlite_vec


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

    def test_init_survives_attribute_error(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.memory_store import MemoryStore

        self._install_bad_connect(monkeypatch, AttributeError("enable_load_extension not available"))

        store = MemoryStore(tmp_path / "noext.db")

        assert store._conn is None
        assert store.count() == 0
        assert store.search([0.1] * 4, top_k=5) == []
        store.close()

    def test_init_survives_operational_error(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import sqlite3 as _sqlite3

        from trw_mcp.state.memory_store import MemoryStore

        self._install_bad_connect(monkeypatch, _sqlite3.OperationalError("not authorized"))

        store = MemoryStore(tmp_path / "opfail.db")

        assert store._conn is None
        store.close()
