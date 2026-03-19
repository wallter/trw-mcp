"""Client-side embedding generation — PRD-CORE-033.

Generates 384-dimensional embeddings using sentence-transformers
``all-MiniLM-L6-v2`` for cross-project knowledge sharing via pgvector
semantic search. Graceful degradation when sentence-transformers is not
installed (optional ``[ai]`` extra).

This module is a **thin wrapper** around
:class:`trw_memory.embeddings.local.LocalEmbeddingProvider`. It keeps the
original module-level singleton API (``embed``, ``embed_batch``,
``embedding_available``, ``embedding_dim``, ``_EMBEDDING_DIM``) so that
existing callers and test patches continue to work unchanged.
"""

from __future__ import annotations

import structlog
from trw_memory.embeddings.local import LocalEmbeddingProvider

logger = structlog.get_logger(__name__)

# Public constant — external code references this directly.
_EMBEDDING_DIM = 384
_MODEL_NAME = "all-MiniLM-L6-v2"

# Module-level singleton backed by LocalEmbeddingProvider.
# Tests monkeypatch ``_model`` to ``None`` in their autouse fixture;
# keep the attribute so that pattern still works.
_provider: LocalEmbeddingProvider | None = None
_model: object | None = None  # kept for test-patching compatibility


def _load_model() -> object | None:
    """Load the embedding model via :class:`LocalEmbeddingProvider`.

    Returns the underlying model object (or *None* on failure).
    The provider instance is cached at module level — repeated calls are cheap.

    Tests may monkeypatch this function to return a mock model.
    """
    global _provider, _model
    # Fast-path: already loaded.
    if _model is not None:
        return _model

    if _provider is None:
        _provider = LocalEmbeddingProvider(model_name=_MODEL_NAME, dim=_EMBEDDING_DIM)

    if _provider.available():
        # Cache the raw model object so the ``_model is not None`` guard works.
        _model = _provider._model
        return _model

    return None


def embed(text: str) -> list[float] | None:
    """Generate a 384-dimensional embedding vector for *text*.

    Returns ``None`` when sentence-transformers is not installed or the
    model fails to load. Callers should treat ``None`` as "embedding
    unavailable" and skip publication to the backend.

    Args:
        text: The text to embed (typically ``summary + " " + detail``).

    Returns:
        List of 384 floats, or None if embedding is unavailable.
    """
    if not text.strip():
        return None

    model = _load_model()
    if model is None:
        return None

    try:
        vector = model.encode(text, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [float(v) for v in vector]
    except Exception:  # justified: boundary, sentence-transformers can raise arbitrary errors
        logger.warning("embedding_generation_failed", text_length=len(text))
        return None


def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Generate embeddings for multiple texts in one call.

    More efficient than calling :func:`embed` in a loop because
    sentence-transformers batches the encoding internally.

    Args:
        texts: List of texts to embed.

    Returns:
        List of embedding vectors (or None for failures), same length as *texts*.
    """
    if not texts:
        return []

    model = _load_model()
    if model is None:
        return [None] * len(texts)

    results: list[list[float] | None] = []
    try:
        vectors = model.encode(  # type: ignore[attr-defined]
            [t for t in texts if t.strip()],
            normalize_embeddings=True,
            batch_size=32,
        )
        vec_idx = 0
        for text in texts:
            if not text.strip():
                results.append(None)
            else:
                results.append([float(v) for v in vectors[vec_idx]])
                vec_idx += 1
    except Exception:  # justified: boundary, sentence-transformers batch can raise arbitrary errors
        logger.warning("batch_embedding_failed", text_count=len(texts))
        return [None] * len(texts)

    return results


def embedding_available() -> bool:
    """Check if the embedding model can be loaded.

    Returns True if sentence-transformers is installed and the model loads
    successfully. Useful for feature detection without generating an
    actual embedding.
    """
    return _load_model() is not None


def embedding_dim() -> int:
    """Return the embedding dimensionality (384 for all-MiniLM-L6-v2)."""
    return _EMBEDDING_DIM
