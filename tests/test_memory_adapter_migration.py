"""Migration, backend, and YAML-path tests for state/memory_adapter.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import get_config
from trw_mcp.state.memory_adapter import ensure_migrated, find_yaml_path_for_entry, get_backend
from ._memory_adapter_support import trw_dir_with_entries  # noqa: F401
from ._memory_adapter_support import trw_dir  # noqa: F401

from ._memory_adapter_support import (
    trw_dir,  # noqa: F401
    trw_dir_with_entries,  # noqa: F401
)

from ._memory_adapter_support import (
    trw_dir,  # noqa: F401
    trw_dir_with_entries,  # noqa: F401
)


class TestEnsureMigrated:
    def test_migrates_yaml_entries(self, trw_dir_with_entries: Path) -> None:
        """YAML entries are migrated to SQLite on first call."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        db_path = trw_dir_with_entries / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            result = ensure_migrated(trw_dir_with_entries, backend)
            assert result["migrated"] == 3
            assert result["skipped"] == 0
            assert backend.count() == 3
        finally:
            backend.close()

    def test_sentinel_prevents_remigration(self, trw_dir_with_entries: Path) -> None:
        """Second call is a no-op when sentinel exists."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        db_path = trw_dir_with_entries / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            ensure_migrated(trw_dir_with_entries, backend)
            result = ensure_migrated(trw_dir_with_entries, backend)
            assert result == {"migrated": 0, "skipped": 0}
        finally:
            backend.close()

    def test_fresh_project_writes_sentinel(self, trw_dir: Path) -> None:
        """Empty entries dir still writes sentinel."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        entries_dir = trw_dir / "learnings" / "entries"
        for f in entries_dir.glob("*"):
            f.unlink()
        entries_dir.rmdir()

        db_path = trw_dir / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            result = ensure_migrated(trw_dir, backend)
            assert result == {"migrated": 0, "skipped": 0}
            assert (trw_dir / "memory" / ".migrated").exists()
        finally:
            backend.close()

    def test_field_mapping_correctness(self, trw_dir_with_entries: Path) -> None:
        """Migrated entries map fields correctly (summary→content, impact→importance)."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        db_path = trw_dir_with_entries / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            ensure_migrated(trw_dir_with_entries, backend)
            entry = backend.get("L-test0001")
            assert entry is not None
            assert entry.content == "Test learning about Python"
            assert entry.importance == 0.8
            assert "python" in entry.tags
        finally:
            backend.close()


class TestGetBackend:
    def test_singleton_returns_same_instance(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
        b1 = get_backend(trw_dir)
        b2 = get_backend(trw_dir)
        assert b1 is b2

    def test_auto_migrates_on_first_access(self, trw_dir_with_entries: Path) -> None:
        backend = get_backend(trw_dir_with_entries)
        assert backend.count() == 3

    def test_constructor_reraises_non_corruption_errors(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-corruption init failures should surface instead of forcing recovery."""
        import trw_mcp.state._memory_connection as conn_mod

        class FakeSQLiteBackend:
            init_calls = 0
            recover_calls = 0

            def __init__(self, db_path: Path, dim: int | None = None) -> None:
                del db_path, dim
                type(self).init_calls += 1
                raise RuntimeError("permission denied")

            @staticmethod
            def recover_db(path: Path) -> object:
                del path
                FakeSQLiteBackend.recover_calls += 1
                raise AssertionError("recover_db should not be called for non-corruption errors")

        conn_mod.reset_backend()
        monkeypatch.setattr(conn_mod, "SQLiteBackend", FakeSQLiteBackend)
        monkeypatch.setattr(conn_mod, "ensure_migrated", lambda trw_dir, backend: {"migrated": 0, "skipped": 0})

        with pytest.raises(RuntimeError, match="permission denied"):
            conn_mod.get_backend(trw_dir)

        assert FakeSQLiteBackend.init_calls == 1
        assert FakeSQLiteBackend.recover_calls == 0

    def test_constructor_recovers_corruption_once(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Corruption-like init failures should recover and retry once."""
        import trw_mcp.state._memory_connection as conn_mod

        class FakeRecoveredConn:
            def close(self) -> None:
                return None

        class FakeSQLiteBackend:
            init_calls = 0
            recover_calls = 0

            def __init__(self, db_path: Path, dim: int | None = None) -> None:
                del db_path, dim
                type(self).init_calls += 1
                if type(self).init_calls == 1:
                    raise RuntimeError("database disk image is malformed")
                self.recovered = False

            @staticmethod
            def recover_db(path: Path) -> FakeRecoveredConn:
                del path
                FakeSQLiteBackend.recover_calls += 1
                return FakeRecoveredConn()

            def close(self) -> None:
                return None

        conn_mod.reset_backend()
        (trw_dir / "memory" / "memory.db").touch()
        monkeypatch.setattr(conn_mod, "SQLiteBackend", FakeSQLiteBackend)
        monkeypatch.setattr(conn_mod, "ensure_migrated", lambda trw_dir, backend: {"migrated": 0, "skipped": 0})

        backend = conn_mod.get_backend(trw_dir)

        assert backend is not None
        assert FakeSQLiteBackend.init_calls == 2
        assert FakeSQLiteBackend.recover_calls == 1
        conn_mod.reset_backend()


class TestFindYamlPathForEntry:
    """PRD-FIX-033-FR05: find_yaml_path_for_entry resolves YAML paths."""

    def test_finds_existing_yaml(self, trw_dir: Path) -> None:
        """Finds YAML file when it exists with sanitized name."""
        from trw_mcp.state.persistence import FileStateWriter

        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        yaml_path = entries_dir / "L-test1.yaml"
        writer.write_yaml(yaml_path, {"id": "L-test1", "summary": "test"})

        result = find_yaml_path_for_entry(trw_dir, "L-test1")
        assert result is not None
        assert result.name == "L-test1.yaml"

    def test_missing_entry_returns_none(self, trw_dir: Path) -> None:
        """Returns None when entry does not exist."""
        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        result = find_yaml_path_for_entry(trw_dir, "L-nonexistent")
        assert result is None

    def test_no_entries_dir_returns_none(self, trw_dir: Path) -> None:
        """Returns None when entries directory does not exist."""
        result = find_yaml_path_for_entry(trw_dir, "L-any")
        assert result is None

    def test_date_prefixed_filename(self, trw_dir: Path) -> None:
        """Finds YAML file with date-prefixed filename containing entry ID."""
        from trw_mcp.state.persistence import FileStateWriter

        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        yaml_path = entries_dir / "2026-02-01-L-dated1-summary-words.yaml"
        writer.write_yaml(yaml_path, {"id": "L-dated1", "summary": "test"})

        result = find_yaml_path_for_entry(trw_dir, "L-dated1")
        assert result is not None
        assert "L-dated1" in result.stem
