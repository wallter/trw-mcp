"""Embedding-space fixtures: two model spaces and space-recorded vectors, no model load."""

from __future__ import annotations

from collections.abc import Sequence

from trw_memory.embeddings.provenance import EmbeddingSpace, StoredVector, VectorProvenance

#: The space vectors written before the default model changed (all-MiniLM-L6-v2).
OLD_SPACE = EmbeddingSpace("a" * 64, "trw-declared-encoder-v1:all-MiniLM-L6-v2", 2)
#: The active embedder's space (BAAI/bge-small-en-v1.5).
NEW_SPACE = EmbeddingSpace("b" * 64, "trw-declared-encoder-v1:BAAI/bge-small-en-v1.5", 2)


def stored(vector: Sequence[float], space: EmbeddingSpace | None, text: str = "text") -> StoredVector:
    """A stored vector recorded in *space*; ``None`` means written without provenance."""
    proof = VectorProvenance.for_vector(space, text, list(vector)) if space is not None else None
    return StoredVector(tuple(vector), proof)


class SpaceProvider:
    """A fake embedder that reports *space* and records which role encoded what."""

    def __init__(self, space: EmbeddingSpace | None, vector: Sequence[float] = (1.0, 0.0)) -> None:
        self.space = space
        self.vector = list(vector)
        self.query_calls: list[str] = []
        self.document_calls: list[str] = []

    def embedding_space(self) -> EmbeddingSpace | None:
        return self.space

    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        self.document_calls.append(text)
        return self.vector

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self.vector
