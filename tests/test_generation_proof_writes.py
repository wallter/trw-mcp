"""Existing MCP capture/backfill writes persist actual generated-vector proof."""

from unittest.mock import Mock

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state._memory_backfill import run_backfill_embeddings
from trw_mcp.state._memory_connection import _embed_and_store_returning


class Provider:
    def __init__(self, known=True):
        self.inputs = []
        self.known = known

    def embed(self, text):
        self.inputs.append(text)
        return [0.6, 0.8]

    def embedding_space(self):
        return EmbeddingSpace("a" * 64, "fixture-encoding", 2) if self.known else None


@pytest.fixture
def backend(tmp_path):
    pytest.importorskip("sqlite_vec")
    result = SQLiteBackend(tmp_path / "memory.db", dim=2)
    assert result.vec_available
    yield result
    result.close()


def test_capture_proof_uses_exact_input_and_unknown_replacement_clears(backend, monkeypatch):
    provider = Provider()
    monkeypatch.setattr("trw_mcp.state._memory_connection.get_embedder", lambda: provider)
    text = "Summary  Detail\n"
    assert _embed_and_store_returning(backend, "entry", text) == [0.6, 0.8]
    record = backend.get_vector_records(["entry"], namespace="default")["entry"]
    assert record.provenance.matches(provider.embedding_space(), text, record.embedding)
    assert provider.inputs == [text]
    provider.known = False
    _embed_and_store_returning(backend, "entry", text)
    assert backend.get_vector_records(["entry"], namespace="default")["entry"].provenance is None


def test_existing_backfill_binds_content_detail_without_extra_inference(backend, tmp_path):
    provider = Provider()
    backend.store(MemoryEntry(id="entry", content="Summary", detail="Detail"))
    result = run_backfill_embeddings(
        tmp_path,
        get_backend=lambda path: backend,
        get_embedder=lambda: provider,
        logger=Mock(),
        namespace="default",
        max_entries=5,
    )
    record = backend.get_vector_records(["entry"], namespace="default")["entry"]
    assert result["embedded"] == 1
    assert record.provenance.matches(provider.embedding_space(), "Summary Detail", record.embedding)
    assert provider.inputs == ["Summary Detail"]
