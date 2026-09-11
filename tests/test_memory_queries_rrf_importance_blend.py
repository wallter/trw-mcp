"""Remove the competing utility decision before final relevance-first ranking."""
from unittest.mock import patch

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._memory_queries import _search_entries


@pytest.mark.parametrize("top_k", [1, 2])
@pytest.mark.parametrize("legacy_alpha", [0.0, 0.7, 1.0])
def test_acquisition_does_not_discard_relevance_for_importance(tmp_path, legacy_alpha, top_k):
    space = EmbeddingSpace("a" * 64, "deterministic-test", 2)

    class Provider:
        def embedding_space(self):
            return space

        def embed(self, text):
            return [1.0, 0.0]

        def available(self):
            return True

    rows = [
        MemoryEntry(id="specific", content="network repair", importance=0.0),
        MemoryEntry(id="incidental", content="repair", importance=1.0),
    ]
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    records = {
        row.id: StoredVector(tuple(vector), VectorProvenance.for_vector(space, f"{row.content} {row.detail}", vector))
        for row, vector in zip(rows, vectors, strict=True)
    }
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for row in rows:
            backend.store(row)
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=Provider()),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig(hybrid_rrf_importance_alpha=legacy_alpha)),
            patch.object(backend, "get_vector_records", return_value=records),
        ):
            selected = _search_entries(backend, "network repair", top_k=top_k)
        assert [entry.id for entry in selected] == ["specific", "incidental"][:top_k]
    finally:
        backend.close()


def test_retired_acquisition_blend_is_not_public_configuration():
    assert "hybrid_rrf_importance_alpha" not in TRWConfig.model_fields
    config = TRWConfig(hybrid_rrf_importance_alpha=0.0)
    assert "hybrid_rrf_importance_alpha" not in config.model_dump()
