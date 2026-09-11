"""Request-local retrieval evidence, never serialized or accepted from a payload.

Identity bindings retain candidates and providers until the request owner releases
this object. An equal dict (including an untrusted remote result) is not evidence.
This is an internal provenance boundary, not a sandbox against arbitrary Python.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from trw_memory.embeddings.provenance import EmbeddingSpace


@dataclass(frozen=True)
class DenseSignal:
    """One actual dense observation in an opaque, request-local vector space."""

    query: str
    embedding_space: object
    source_store: object
    namespace: str
    entry_id: str
    cosine: float


class RecallSignals:
    """Owned by one recall request; only explicit object transfers retain trust."""

    def __init__(self, query: str) -> None:
        self.query = query
        self._bindings: dict[int, tuple[object, DenseSignal]] = {}
        self._spaces: dict[tuple[EmbeddingSpace, tuple[float, ...]], object] = {}
        self._stores: dict[int, tuple[object, object]] = {}

    def get(self, candidate: object) -> DenseSignal | None:
        binding = self._bindings.get(id(candidate))
        return binding[1] if binding is not None and binding[0] is candidate else None

    def transfer(self, source: object, destination: object) -> None:
        """Bind an explicitly produced projection/copy, never match by entry ID."""
        signal = self.get(source)
        if signal is not None:
            self._bindings[id(destination)] = (destination, signal)

    def bind_dense(
        self,
        candidate: object,
        *,
        query: str,
        provider: object,
        query_vector: list[float],
        store: object,
        namespace: str,
        entry_id: str,
        cosine: float,
        verified_space: EmbeddingSpace | None = None,
    ) -> None:
        """Bind successful acquisition evidence; a different query is not ours.

        Callers supply a generation-validated descriptor, not a model name.
        Equal descriptors and exact query vectors permit cross-store comparison.
        Unknown descriptors cannot create evidence. Provider is retained in the
        call contract for compatibility, but is not an identity authority.
        """
        if (
            not isinstance(verified_space, EmbeddingSpace)
            or query != self.query
            or not math.isfinite(cosine)
            or len(query_vector) != verified_space.dimensions
            or not all(map(math.isfinite, query_vector))
        ):
            return
        signature = (verified_space, tuple(query_vector))
        space = self._spaces.setdefault(signature, object())
        store_binding = self._stores.get(id(store))
        if store_binding is None:
            store_binding = (store, object())
            self._stores[id(store)] = store_binding
        signal = DenseSignal(query, space, store_binding[1], namespace, entry_id, cosine)
        self._bindings[id(candidate)] = (candidate, signal)


_ACTIVE: ContextVar[RecallSignals | None] = ContextVar("recall_signals", default=None)


def current_recall_signals() -> RecallSignals | None:
    return _ACTIVE.get()


@contextmanager
def recall_signal_scope(query: str) -> Iterator[RecallSignals]:
    """Activate a fresh collector; nested/exceptional exits restore their owner.

    The returned object can be used explicitly after exit, but is no longer
    ambient. It owns all references and should be released with the request.
    """
    signals = RecallSignals(query)
    token = _ACTIVE.set(signals)
    try:
        yield signals
    finally:
        _ACTIVE.reset(token)
