"""Bounded repair uses actual SQLite vectors and rechecks concurrent input."""

from unittest.mock import patch

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.interface import EntryCursor
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._embedding_repair import repair_page
from trw_mcp.state._memory_queries import _search_entries
from trw_mcp.state._recall_signals import recall_signal_scope


class Provider:
    def __init__(self):
        self.inputs = []
        self.space = EmbeddingSpace("a" * 64, "fixture", 2)
        self.on_embed = lambda: None

    def embedding_space(self):
        return self.space

    def embed(self, text):
        self.inputs.append(text)
        self.on_embed()
        return [1.0, 0.0]

    def available(self):
        return True


@pytest.fixture
def backend(tmp_path):
    pytest.importorskip("sqlite_vec")
    backend = SQLiteBackend(tmp_path / "memory.db", dim=2)
    assert backend.vec_available
    yield backend
    backend.close()


def test_legacy_repair_update_repair_recall(backend):
    provider = Provider()
    entry = MemoryEntry(id="entry", content="restore connectivity")
    backend.store(entry)
    backend.upsert_vector(entry.id, [0.0, 1.0], namespace="default")

    def signal():
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=provider),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            recall_signal_scope("network repair") as signals,
        ):
            rows = _search_entries(backend, "network repair", top_k=1)
            return signals.get(rows[0]) if rows else None

    assert signal() is None
    first = repair_page(backend, provider, max_entries=1)
    assert first["repaired"] == 1 and signal() is not None
    assert backend.get("entry", namespace="default").updated_at == entry.updated_at
    before = len(provider.inputs)
    assert repair_page(backend, provider, max_entries=1)["skipped"] == 1
    assert len(provider.inputs) == before
    backend.update("entry", namespace="default", content="reset the connection")
    assert signal() is None
    assert repair_page(backend, provider, max_entries=1)["repaired"] == 1
    assert signal() is not None


def test_page_cursor_advances_past_valid_rows_and_does_not_touch_other_namespace(backend):
    provider = Provider()
    for i in range(3):
        backend.store(MemoryEntry(id=str(i), content="repair"))
    backend.store(MemoryEntry(id="other", content="repair", namespace="other"))
    first = repair_page(backend, provider, max_entries=2)
    assert first["inspected"] == first["repaired"] == 2
    assert first["status"] == "partial"
    second = repair_page(backend, provider, max_entries=2, after=EntryCursor(**first["next_cursor"]))
    assert second["repaired"] == 1 and second["status"] == "completed"
    assert backend.get_vector_records(["other"], namespace="other") == {}


def test_edit_during_inference_does_not_replace_old_vector(backend):
    provider = Provider()
    backend.store(MemoryEntry(id="entry", content="old"))
    backend.upsert_vector("entry", [0.0, 1.0], namespace="default")
    provider.on_embed = lambda: backend.update("entry", namespace="default", content="new")
    result = repair_page(backend, provider, max_entries=1)
    assert result["changed"] == 1 and result["repaired"] == 0
    assert backend.get_vector_records(["entry"], namespace="default")["entry"].embedding == (0.0, 1.0)


def test_unpersisted_write_is_failed_not_repaired(backend):
    provider = Provider()
    backend.store(MemoryEntry(id="entry", content="repair"))
    with patch.object(backend, "upsert_vector", return_value=None):
        result = repair_page(backend, provider, max_entries=1)
    assert result["failed"] == 1 and result["repaired"] == 0


def test_unknown_or_changed_provider_does_not_stamp(backend):
    provider = Provider()
    backend.store(MemoryEntry(id="entry", content="repair"))
    provider.space = None
    assert repair_page(backend, provider, max_entries=1)["status"] == "blocked"
    assert provider.inputs == []
    provider.space = EmbeddingSpace("a" * 64, "fixture", 2)
    provider.on_embed = lambda: setattr(provider, "space", EmbeddingSpace("b" * 64, "fixture", 2))
    assert repair_page(backend, provider, max_entries=1)["failed"] == 1
    assert backend.get_vector_records(["entry"], namespace="default") == {}


def test_actual_facade_targets_existing_db_not_singleton(tmp_path, monkeypatch):
    from trw_mcp.state import _memory_connection as connection

    pytest.importorskip("sqlite_vec")
    trw = tmp_path / ".trw"
    db = trw / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    backend = SQLiteBackend(db, dim=2)
    backend.store(MemoryEntry(id="entry", content="repair"))
    backend.close()
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw)
    monkeypatch.setattr(connection, "get_embedder", lambda: Provider())
    monkeypatch.setattr(connection, "get_backend", lambda *a: pytest.fail("singleton backend used"))
    assert connection.repair_embeddings(trw, max_entries=1)["repaired"] == 1
    with pytest.raises(ValueError, match="target project"):
        connection.repair_embeddings(tmp_path / "wrong", max_entries=1)
    backend = SQLiteBackend(db, dim=2)
    try:
        assert backend.get_vector_records(["entry"], namespace="default")["entry"].provenance is not None
    finally:
        backend.close()


def test_facade_refuses_creation_and_old_schema_before_model(tmp_path, monkeypatch):
    import sqlite3

    from trw_mcp.state import _memory_connection as connection

    trw = tmp_path / ".trw"
    trw.mkdir()
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw)
    monkeypatch.setattr(connection, "get_embedder", lambda: pytest.fail("model initialization before validation"))
    with pytest.raises(RuntimeError, match="existing memory"):
        connection.repair_embeddings(trw)
    assert not (trw / "memory").exists()
    (trw / "memory").mkdir()
    db = sqlite3.connect(trw / "memory" / "memory.db")
    db.execute("PRAGMA user_version=5")
    db.close()
    with pytest.raises(ValueError, match="schema separately"):
        connection.repair_embeddings(trw)


def test_becoming_canary_during_inference_is_not_written(backend):
    provider = Provider()
    backend.store(MemoryEntry(id="entry", content="repair"))
    provider.on_embed = lambda: backend.update("entry", namespace="default", metadata={"system_canary": "true"})
    result = repair_page(backend, provider, max_entries=1)
    assert result["changed"] == 1 and result["status"] == "partial"
    assert backend.get_vector_records(["entry"], namespace="default") == {}


def test_facade_requires_write_permission_before_db_or_model(tmp_path, monkeypatch):
    from trw_memory.exceptions import AuthorizationError
    from trw_memory.models.config import MemoryConfig

    from trw_mcp.state import _memory_connection as connection

    trw = tmp_path / ".trw"
    trw.mkdir()
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw)
    monkeypatch.setattr(connection, "get_embedder", lambda: pytest.fail("unauthorized model init"))
    config = MemoryConfig(rbac_enabled=True, default_role="reader")
    monkeypatch.setattr("trw_memory.models.config.MemoryConfig", lambda: config)
    with pytest.raises(AuthorizationError):
        connection.repair_embeddings(trw)
    assert not (trw / "memory").exists()


def test_cli_dispatch_reaches_real_repair_and_storage(tmp_path, monkeypatch, capsys):
    import json

    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import _run_update_project
    from trw_mcp.state import _memory_connection as connection

    pytest.importorskip("sqlite_vec")
    trw = tmp_path / ".trw"
    db = trw / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    backend = SQLiteBackend(db, dim=2)
    backend.store(MemoryEntry(id="entry", content="repair"))
    backend.close()
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw)
    monkeypatch.setattr(connection, "get_embedder", lambda: Provider())
    monkeypatch.setattr("trw_mcp.bootstrap.update_project", lambda *a, **kw: pytest.fail("framework update invoked"))
    args = _build_arg_parser().parse_args(["update-project", str(tmp_path), "--repair-embeddings", "1"])
    with pytest.raises(SystemExit) as caught:
        _run_update_project(args)
    assert caught.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["repaired"] == 1 and output["status"] == "completed"
    backend = SQLiteBackend(db, dim=2)
    try:
        record = backend.get_vector_records(["entry"], namespace="default")["entry"]
        assert record.provenance.matches(Provider().embedding_space(), "repair ", record.embedding)
    finally:
        backend.close()


@pytest.mark.parametrize("missing_provider", [True, False], ids=["no-provider", "unknown-identity"])
def test_facade_blocks_without_qualified_provider_before_backend_open(tmp_path, monkeypatch, missing_provider):
    from trw_mcp.state import _memory_connection as connection

    trw = tmp_path / ".trw"
    db = trw / "memory" / "memory.db"
    db.parent.mkdir(parents=True)
    backend = SQLiteBackend(db, dim=2)
    backend.store(MemoryEntry(id="entry", content="preserve this memory"))
    backend.close()
    before = db.read_bytes()
    provider = Provider()
    provider.space = None
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw)
    monkeypatch.setattr(connection, "get_embedder", lambda: None if missing_provider else provider)
    monkeypatch.setattr(connection, "SQLiteBackend", lambda *a, **kw: pytest.fail("writer backend opened"))

    assert connection.repair_embeddings(trw) == {
        "status": "blocked",
        "reason": "provider_identity_unavailable",
        "inspected": 0,
        "repaired": 0,
        "skipped": 0,
        "changed": 0,
        "failed": 0,
        "next_cursor": None,
    }
    assert db.read_bytes() == before
    assert provider.inputs == []
