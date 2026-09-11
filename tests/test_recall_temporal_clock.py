"""One expiry reference survives project/user/external acquisition and postfilter."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry
from trw_memory.retrieval.temporal_selection import TemporalSelection

from tests._tools_learning_shared import set_project_root  # noqa: F401
from trw_mcp.state import _memory_recall
from trw_mcp.state._external_store import _query_external_backend
from trw_mcp.state.memory_adapter import get_backend


@pytest.mark.parametrize("route", ["project", "user", "external"])
@pytest.mark.parametrize("query", ["Clockfixture", "*"])
def test_frozen_reference_reaches_each_acquisition_route(
    set_project_root: Path, monkeypatch: pytest.MonkeyPatch, route: str, query: str
) -> None:
    trw_dir = set_project_root / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = get_backend(trw_dir)
    backend.store(MemoryEntry(id="L-clock", content="Clockfixture expiry", expires="2022-01-01"))
    selection = TemporalSelection(
        reference_time=datetime(2022, 1, 1, tzinfo=timezone.utc), exclude_system_canaries=True
    )
    if route == "project":
        monkeypatch.setattr(_memory_recall, "TemporalSelection", lambda **kwargs: selection)
        hits = _memory_recall.recall_learnings(
            trw_dir, query, max_results=3, allow_cold_embedding_init=False, include_tiers=["project"]
        )
        assert [row["id"] for row in hits] == ["L-clock"]
    else:
        query_fn = _memory_recall._query_user_backend if route == "user" else _query_external_backend
        hits = query_fn(
            backend,
            query,
            tags=None,
            mem_status=None,
            min_impact=0,
            max_results=3,
            is_wildcard=query == "*",
            allow_cold_embedding_init=False,
            temporal_selection=selection,
        )
        assert [row.id for row in hits] == ["L-clock"]


def test_frozen_reference_reaches_real_hybrid_prior(set_project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance
    from trw_memory.retrieval import pipeline

    from trw_mcp.state import _memory_connection
    from trw_mcp.state._memory_queries import _search_entries

    trw_dir = set_project_root / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = get_backend(trw_dir)
    backend.store(MemoryEntry(id="L-hybridclock", content="Clockfixture expiry", expires="2022-01-01"))
    selection = TemporalSelection(reference_time=datetime(2022, 1, 1, tzinfo=timezone.utc))

    class Provider:
        def embedding_space(self):
            return EmbeddingSpace("a" * 64, "clock-fixture", 384)

        def available(self):
            return True

        def embed(self, text):
            return [0.1] * 384

    entry = backend.get("L-hybridclock", namespace="default")
    vector = Provider().embed(entry.content)
    record = StoredVector(
        tuple(vector),
        VectorProvenance.for_vector(
            Provider().embedding_space(),
            f"{entry.content} {entry.detail}",
            vector,
        ),
    )

    def qualified_records(ids, *, namespace):
        assert namespace == "default"
        assert ids == ["L-hybridclock"]
        return {"L-hybridclock": record}

    monkeypatch.setattr(backend, "get_vector_records", qualified_records)
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: Provider())
    original = pipeline.hybrid_search
    observed = []

    def checked_hybrid(*args, **kwargs):
        assert kwargs["validity_reference_time"] is selection.reference_time
        hits = original(*args, **kwargs)
        assert [row.id for row in hits] == ["L-hybridclock"], "Hybrid must not silently fall back after expiry loss"
        observed.append(True)
        return hits

    monkeypatch.setattr(pipeline, "hybrid_search", checked_hybrid)
    hits = _search_entries(backend, "Clockfixture", allow_cold_embedding_init=False, temporal_selection=selection)
    assert [row.id for row in hits] == ["L-hybridclock"]
    assert observed == [True]
