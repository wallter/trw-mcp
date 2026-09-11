"""Relevant lexical candidates must survive acquisition before utility ranking."""

from pathlib import Path

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state._memory_queries import _keyword_search


def test_multi_term_match_not_hidden_by_per_term_importance_cap(tmp_path: Path) -> None:
    """An all-term entry is not lost behind high-importance one-term matches."""
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for token in ("telemetry", "discovery"):
            for index in range(6):
                backend.store(
                    MemoryEntry(
                        id=f"{token}-{index}",
                        content=f"{token} unrelated single-term incident {index}",
                        importance=0.95,
                    )
                )
        backend.store(
            MemoryEntry(
                id="relevant",
                content="telemetry discovery failure explained",
                importance=0.6,
            )
        )
        results = _keyword_search(
            backend,
            "telemetry discovery",
            top_k=5,
            mem_status=MemoryStatus.ACTIVE,
        )
        assert len(results) <= 5
        assert "relevant" in [entry.id for entry in results]
    finally:
        backend.close()


def test_multitoken_filters_and_literal_terms(tmp_path: Path) -> None:
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        entries = [
            MemoryEntry(id="wanted", content=r"a_b 50% c\d", tags=['a"b'], importance=0.6),
            MemoryEntry(id="wildcard", content="axb 500 cxd", tags=['a"b'], importance=0.9),
            MemoryEntry(id="other-ns", content=r"a_b 50% c\d", namespace="secret", tags=['a"b']),
            MemoryEntry(id="other-tag", content=r"a_b 50% c\d", tags=['a"bc']),
            MemoryEntry(id="low", content=r"a_b 50% c\d", tags=['a"b'], importance=0.1),
            MemoryEntry(id="inactive", content=r"a_b 50% c\d", tags=['a"b'], status=MemoryStatus.RESOLVED),
        ]
        for entry in entries:
            backend.store(entry)
        backend._fts_available = False
        results = backend.search(
            "unused",
            keyword_tokens=["a_b", "50%", r"c\d"],
            top_k=5,
            namespace="default",
            tags=['a"b'],
            status=MemoryStatus.ACTIVE,
            min_importance=0.5,
        )
        assert [entry.id for entry in results] == ["wanted"]
    finally:
        backend.close()


def test_temporal_selection_precedes_relevance_cap(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from trw_memory.retrieval.temporal_selection import TemporalSelection

    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for index in range(6):
            backend.store(
                MemoryEntry(
                    id=f"closed-{index}",
                    content="telemetry discovery",
                    importance=0.95,
                    valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    invalid_from=datetime(2021, 1, 1, tzinfo=timezone.utc),
                    invalidated_by="current",
                )
            )
        backend.store(MemoryEntry(id="current", content="telemetry discovery", importance=0.6))
        results = _keyword_search(
            backend,
            "telemetry discovery",
            top_k=1,
            mem_status=MemoryStatus.ACTIVE,
            temporal_selection=TemporalSelection(),
        )
        assert [entry.id for entry in results] == ["current"]
    finally:
        backend.close()


def test_relevance_query_replays_exact_order_and_filters(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from trw_memory.storage import _temporal_fetch

    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        backend.store(MemoryEntry(id="one", content="telemetry", importance=0.95))
        backend.store(MemoryEntry(id="both", content="telemetry discovery", importance=0.6))
        original = _temporal_fetch._select_stream
        calls = []

        def fail_first(connection, query, *args, **kwargs):
            calls.append(query.build())
            if len(calls) == 1:
                raise sqlite3.OperationalError("Could not decode to UTF-8")
            return original(connection, query, *args, **kwargs)

        monkeypatch.setattr(_temporal_fetch, "_select_stream", fail_first)
        results = backend.search(
            "unused",
            keyword_tokens=["telemetry", "discovery"],
            top_k=1,
            namespace="default",
        )
        assert [entry.id for entry in results] == ["both"]
        assert len(calls) == 2
        assert calls[0] == calls[1]
        assert "namespace = ?" in calls[0][0]
    finally:
        backend.close()


def test_single_query_and_id_precedence_are_unchanged(tmp_path: Path) -> None:
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        backend.store(MemoryEntry(id="L-abcd", content="elsewhere", importance=0.1))
        backend.store(MemoryEntry(id="single", content="telemetry", importance=0.95))
        backend.store(MemoryEntry(id="both", content="telemetry discovery", importance=0.6))
        assert backend.search("telemetry", top_k=1)[0].id == "single"
        assert _keyword_search(backend, "L-abcd telemetry discovery", top_k=1)[0].id == "L-abcd"
    finally:
        backend.close()


def test_non_ascii_keyword_preserves_sqlite_literal_matching(tmp_path: Path) -> None:
    """Python lowercasing must not change SQLite's non-ASCII LIKE inputs."""
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        backend.store(MemoryEntry(id="city", content="İstanbul"))
        assert [entry.id for entry in backend.search("İstanbul")] == ["city"]
        results = backend.search("unused", keyword_tokens=["İstanbul", "nonmatch"])
        assert [entry.id for entry in results] == ["city"]
    finally:
        backend.close()


@pytest.mark.parametrize("compact", [False, True])
def test_registered_recall_retains_symptom_match_after_acquisition(
    tmp_project: Path, monkeypatch, compact: bool
) -> None:
    """Exercise schema, storage, ranking and output budget, not a ranked-list double."""
    import asyncio

    from fastmcp import Client

    from tests.conftest import make_test_server
    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import get_backend

    monkeypatch.setattr(get_config(), "embeddings_enabled", False)
    from trw_mcp.tools import learning

    original_rank = learning.rank_by_utility
    observed_rankings = []

    def observe_rank(*args, **kwargs):
        ranked = original_rank(*args, **kwargs)
        observed_rankings.append(
            {
                "count": len(ranked),
                "first": (ranked[0]["id"], ranked[0]["combined_score"]) if ranked else None,
                "target": [
                    (i + 1, entry["combined_score"]) for i, entry in enumerate(ranked) if entry["id"] == "L-symptom"
                ],
            }
        )
        return ranked

    monkeypatch.setattr(learning, "rank_by_utility", observe_rank)
    backend = get_backend(tmp_project / ".trw")
    for token in ("read-only", "project", "tool", "discovery"):
        for index in range(26):
            backend.store(
                MemoryEntry(
                    id=f"{token}-{index}",
                    content=f"{token} distinct archived investigation number {index}",
                    importance=0.95,
                )
            )
    backend.store(
        MemoryEntry(
            id="L-symptom",
            content="Telemetry fail-open boundaries must catch FileStateWriter's StateError, not only raw OSError",
            detail=(
                "Optional MCP security telemetry blocked real tool discovery and recall topic tests "
                "on a read-only project. Catch wrapped persistence errors at the optional telemetry "
                "boundary, preserving warning signals and authorization filters."
            ),
            importance=0.6,
        )
    )

    async def recall():
        async with Client(make_test_server("learning")) as client:
            result = await client.call_tool(
                "trw_recall",
                {
                    "query": "read-only project tool discovery fails",
                    "max_results": 5,
                    "token_budget": 1800,
                    "include_tiers": ["project"],
                    "compact": compact,
                },
            )
            assert not result.is_error
            return result.structured_content

    acquired = _keyword_search(
        backend, "read-only project tool discovery fails", top_k=25, mem_status=MemoryStatus.ACTIVE
    )
    assert "L-symptom" in [entry.id for entry in acquired]
    response = asyncio.run(recall())
    assert len(response["learnings"]) <= 5
    assert response["tokens_used"] <= 1800
    assert "L-symptom" in [entry["id"] for entry in response["learnings"]], observed_rankings


@pytest.mark.parametrize("compact", [False, True])
def test_registered_recall_keeps_semantic_only_candidate(tmp_project: Path, monkeypatch, compact: bool) -> None:
    """Actual tool/storage/fusion, deterministic vector fixture, no model call."""
    import asyncio

    from fastmcp import Client

    from tests.conftest import make_test_server
    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import get_backend

    class Provider:
        model_name = "deterministic-fixture"

        def embedding_space(self):
            return EmbeddingSpace("a" * 64, "fixture-document-v1", 2)

        def available(self):
            return True

        def embed(self, query):
            return [1.0, 0.0]

    monkeypatch.setattr(get_config(), "embeddings_enabled", True)
    monkeypatch.setattr("trw_mcp.state._memory_connection.get_initialized_embedder", lambda: provider)
    provider = Provider()
    backend = get_backend(tmp_project / ".trw")
    backend.store(MemoryEntry(id="L-semantic", content="Restore connectivity", importance=0.1))
    backend.store(MemoryEntry(id="L-literal", content="Network repair", importance=0.9))
    backend.store(MemoryEntry(id="L-unrelated", content="Apples and oranges", importance=0.99))

    def records(ids, *, namespace):
        result = {}
        for entry_id, vector in {"L-semantic": [1.0, 0.0], "L-literal": [0.0, 1.0], "L-unrelated": [-1.0, 0.0]}.items():
            entry = backend.get(entry_id, namespace=namespace)
            result[entry_id] = StoredVector(
                tuple(vector),
                VectorProvenance.for_vector(provider.embedding_space(), f"{entry.content} {entry.detail}", vector),
            )
        return result

    monkeypatch.setattr(backend, "get_vector_records", records)

    async def recall():
        async with Client(make_test_server("learning")) as client:
            result = await client.call_tool(
                "trw_recall",
                {"query": "network repair", "max_results": 2, "compact": compact, "include_tiers": ["project"]},
            )
            assert not result.is_error
            return result.structured_content

    response = asyncio.run(recall())
    ids = [entry["id"] for entry in response["learnings"]]
    assert "L-semantic" in ids
    assert "L-unrelated" not in ids
    assert len(ids) <= 2
    assert all("embedding_space" not in entry and "cosine" not in entry for entry in response["learnings"])
