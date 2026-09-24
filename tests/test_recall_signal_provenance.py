"""Dense evidence belongs to actual request-local candidates, not public fields."""

from __future__ import annotations

import pytest
from trw_memory.embeddings.provenance import EmbeddingSpace

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


def test_nonfinite_observations_and_query_vectors_are_rejected() -> None:
    signals = RecallSignals("network repair")
    candidate = object()
    _bind(signals, candidate, Provider(), object(), cosine=float("nan"))
    assert signals.get(candidate) is None

    _bind(signals, candidate, Provider(), object(), query_vector=[float("inf")])
    assert signals.get(candidate) is None
