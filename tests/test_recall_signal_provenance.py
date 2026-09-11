"""Dense evidence belongs to actual request-local candidates, not public fields."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._memory_queries import _search_entries
from trw_mcp.state._memory_transforms import _memory_to_learning_dict
from trw_mcp.state._recall_signals import RecallSignals, current_recall_signals, recall_signal_scope


class Provider:
    model_name = "deterministic-test-space"

    def embedding_space(self) -> EmbeddingSpace:
        return EmbeddingSpace("a" * 64, "fixture-document-v1", 2)

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def available(self) -> bool:
        return True


def _bind(signals: RecallSignals, entry: object, provider: object, store: object, **overrides: object) -> None:
    kwargs = {
        "query": "network repair",
        "provider": provider,
        "query_vector": [1.0, 0.0],
        "store": store,
        "namespace": "default",
        "entry_id": "semantic",
        "cosine": 1.0,
        "verified_space": Provider().embedding_space(),
    }
    kwargs.update(overrides)
    signals.bind_dense(entry, **kwargs)


def test_identity_and_explicit_transfer_not_payload_fields() -> None:
    candidate = {"id": "semantic", "cosine": 1.0, "embedding_space": "claimed"}
    with recall_signal_scope("network repair") as signals:
        assert signals.get(candidate) is None
        _bind(signals, candidate, Provider(), object())
        copied = dict(candidate)
        assert signals.get(copied) is None
        signals.transfer(candidate, copied)
        assert signals.get(copied) is signals.get(candidate)
    assert current_recall_signals() is None


def test_context_isolation_restores_after_exception() -> None:
    with recall_signal_scope("network repair") as outer:
        candidate = object()
        _bind(outer, candidate, Provider(), object())
        with pytest.raises(RuntimeError), recall_signal_scope("network repair") as inner:
            assert current_recall_signals() is inner
            assert inner.get(candidate) is None
            raise RuntimeError("request failed")
        assert current_recall_signals() is outer
    assert current_recall_signals() is None


def test_space_requires_equal_descriptor_query_and_vector_not_provider_identity() -> None:
    signals = RecallSignals("network repair")
    provider, store = Provider(), object()
    candidates = [object() for _ in range(7)]
    _bind(signals, candidates[0], provider, store)
    _bind(signals, candidates[1], provider, object())
    _bind(signals, candidates[2], Provider(), store)
    _bind(signals, candidates[3], provider, store, query_vector=[0.0, 1.0])
    _bind(signals, candidates[4], provider, store, verified_space=EmbeddingSpace("b" * 64, "fixture-document-v1", 2))
    _bind(signals, candidates[5], provider, store, query="another query")
    _bind(signals, candidates[6], provider, store, verified_space=None)
    evidence = [signals.get(candidate) for candidate in candidates]
    assert evidence[0].embedding_space == evidence[1].embedding_space == evidence[2].embedding_space
    assert evidence[0].source_store is not evidence[1].source_store
    assert all(evidence[i].embedding_space != evidence[0].embedding_space for i in (3, 4))
    assert evidence[5] is None
    assert evidence[6] is None


def _records(backend, vectors, namespace="default"):
    records = {}
    for entry_id, vector in vectors.items():
        entry = backend.get(entry_id, namespace=namespace)
        records[entry_id] = StoredVector(
            tuple(vector),
            VectorProvenance.for_vector(Provider().embedding_space(), f"{entry.content} {entry.detail}", vector),
        )
    return records


@pytest.mark.parametrize("compact", [False, True])
def test_actual_acquisition_projects_trusted_raw_scores_without_fields(tmp_path, compact: bool) -> None:
    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(MemoryEntry(id="semantic", content="restore connectivity", importance=0.1))
    backend.store(MemoryEntry(id="literal", content="network repair", importance=1.0))
    provider = Provider()
    try:
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=provider),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            patch.object(
                backend,
                "get_vector_records",
                return_value=_records(backend, {"semantic": [1.0, 0.0], "literal": [0.6, 0.8]}),
            ),
            recall_signal_scope("network repair") as signals,
        ):
            entries = _search_entries(backend, "network repair", top_k=5)
            projected = [_memory_to_learning_dict(entry, compact=compact) for entry in entries]
            by_id = {entry["id"]: entry for entry in projected}
            assert signals.get(by_id["semantic"]).cosine == 1.0
            assert signals.get(by_id["literal"]).cosine == pytest.approx(0.6)
            assert signals.get(dict(by_id["semantic"])) is None
            assert all("cosine" not in entry and "embedding_space" not in entry for entry in projected)
            assert all(not entry.metadata for entry in entries)
        assert [_memory_to_learning_dict(entry, compact=compact) for entry in entries] == projected
    finally:
        backend.close()


def test_failed_hybrid_discards_observations_before_keyword_fallback(tmp_path) -> None:
    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(MemoryEntry(id="semantic", content="network repair"))

    def failed_hybrid(**kwargs):
        kwargs["dense_observer"]((("semantic", 1.0),))
        raise RuntimeError("fusion failed after observation")

    try:
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=Provider()),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            patch.object(backend, "get_vector_records", return_value=_records(backend, {"semantic": [1.0, 0.0]})),
            patch("trw_memory.retrieval.pipeline.hybrid_search", side_effect=failed_hybrid),
            recall_signal_scope("network repair") as signals,
        ):
            entries = _search_entries(backend, "network repair")
            assert entries
            assert all(signals.get(entry) is None for entry in entries)
    finally:
        backend.close()


def test_project_user_federation_retains_source_and_common_space(tmp_path) -> None:
    from trw_mcp.state._memory_recall import _federate_user_tier

    project = SQLiteBackend(tmp_path / "project.db")
    user = SQLiteBackend(tmp_path / "user.db")
    project.store(MemoryEntry(id="project-hit", content="network repair"))
    user.store(MemoryEntry(id="user-hit", content="restore connectivity", namespace="user:test"))
    provider = Provider()
    try:
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=provider),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            patch.object(project, "get_vector_records", return_value=_records(project, {"project-hit": [0.6, 0.8]})),
            patch.object(
                user, "get_vector_records", return_value=_records(user, {"user-hit": [1.0, 0.0]}, "user:test")
            ),
            patch("trw_mcp.state._memory_recall.user_scope_present", return_value=True),
            patch("trw_mcp.state._memory_recall.peek_user_backend", return_value=user),
            recall_signal_scope("network repair") as signals,
        ):
            project_hits = _search_entries(project, "network repair", top_k=5)
            merged = _federate_user_tier(
                project_hits,
                "network repair",
                tags=None,
                mem_status=None,
                min_impact=0.0,
                max_results=5,
                is_wildcard=False,
                allow_cold_embedding_init=True,
            )
            rows = [_memory_to_learning_dict(entry) for entry in merged]
            assert [row["id"] for row in rows] == ["project-hit", "user-hit"]
            project_signal, user_signal = (signals.get(row) for row in rows)
            assert project_signal.embedding_space == user_signal.embedding_space
            assert project_signal.source_store is not user_signal.source_store
            assert project_signal.namespace == "default"
            assert user_signal.namespace == "user:test"
            assert user_signal.cosine == 1.0
    finally:
        project.close()
        user.close()


def test_nonfinite_observations_and_query_vectors_are_rejected() -> None:
    signals = RecallSignals("network repair")
    candidate = object()
    _bind(signals, candidate, Provider(), object(), cosine=float("nan"))
    assert signals.get(candidate) is None

    _bind(signals, candidate, Provider(), object(), query_vector=[float("inf")])
    assert signals.get(candidate) is None


@pytest.mark.parametrize("requested_namespace", ["default", None])
def test_acquisition_loads_candidate_namespace_before_dense_binding(tmp_path, requested_namespace) -> None:
    """Real acquisition and vector-loader SQL; ordinary blob tables replace vec0."""
    import sqlite3
    import struct
    import threading

    from trw_memory.storage._vector_ops import get_vector_records

    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(MemoryEntry(id="same", content="network repair", namespace="default"))
    backend.store(MemoryEntry(id="user-hit", content="network repair", namespace="user:test"))
    vectors = sqlite3.connect(":memory:")
    vectors.execute(
        "CREATE TABLE vec_index (rowid INTEGER PRIMARY KEY, namespace TEXT, entry_id TEXT, provenance_json TEXT)"
    )
    vectors.execute("CREATE TABLE vec_memories (rowid INTEGER PRIMARY KEY, embedding BLOB)")
    for rowid, namespace, entry_id, vector in [
        (1, "default", "same", [0.0, 1.0]),
        (2, "project:other", "same", [1.0, 0.0]),
        (3, "user:test", "user-hit", [0.6, 0.8]),
    ]:
        proof = VectorProvenance.for_vector(Provider().embedding_space(), "network repair ", vector)
        vectors.execute("INSERT INTO vec_index VALUES (?, ?, ?, ?)", (rowid, namespace, entry_id, proof.to_json()))
        vectors.execute("INSERT INTO vec_memories VALUES (?, ?)", (rowid, struct.pack("2f", *vector)))
    calls = []

    def qualified_read(ids, *, namespace=None):
        calls.append(namespace)
        return get_vector_records(vectors, threading.RLock(), vec_available=True, entry_ids=ids, namespace=namespace)

    try:
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=Provider()),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            patch.object(backend, "get_vector_records", side_effect=qualified_read),
            recall_signal_scope("network repair") as signals,
        ):
            entries = _search_entries(backend, "network repair", namespace=requested_namespace, top_k=5)
            by_id = {entry.id: entry for entry in entries}
            assert signals.get(by_id["same"]).cosine == 0.0
            assert set(calls) == ({"default"} if requested_namespace is not None else {"default", "user:test"})
            assert None not in calls
            if requested_namespace is None:
                assert signals.get(by_id["user-hit"]).cosine == pytest.approx(0.6)
    finally:
        backend.close()
        vectors.close()


@pytest.mark.parametrize("invalid", ["unknown", "stale-input", "wrong-model", "wrong-bytes", "unknown-provider"])
def test_unverified_vectors_cannot_bind_or_displace_keyword_candidate(tmp_path, invalid: str) -> None:
    """Reject incompatible generation evidence before acquisition's candidate cut."""
    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(MemoryEntry(id="semantic", content="restore connectivity", importance=1.0))
    backend.store(MemoryEntry(id="literal", content="network repair", importance=0.1))
    provider = Provider()
    space = provider.embedding_space()
    vectors = {"semantic": [1.0, 0.0], "literal": [0.0, 1.0]}
    records = {}
    for entry_id, vector in vectors.items():
        entry = backend.get(entry_id, namespace="default")
        text = f"{entry.content} {entry.detail}"
        proof = VectorProvenance.for_vector(
            EmbeddingSpace("b" * 64, space.encoding, 2) if invalid == "wrong-model" else space,
            "old input" if invalid == "stale-input" else text,
            [-1.0, 0.0] if invalid == "wrong-bytes" else vector,
        )
        records[entry_id] = StoredVector(tuple(vector), None if invalid == "unknown" else proof)
    try:
        with (
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=provider),
            patch("trw_mcp.models.config.get_config", return_value=TRWConfig()),
            patch.object(provider, "embedding_space", return_value=None if invalid == "unknown-provider" else space),
            patch.object(backend, "get_vector_records", return_value=records),
            recall_signal_scope("network repair") as signals,
        ):
            entries = _search_entries(backend, "network repair", top_k=1)
            assert [entry.id for entry in entries] == ["literal"]
            assert all(signals.get(entry) is None for entry in entries)
    finally:
        backend.close()
