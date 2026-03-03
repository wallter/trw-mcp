"""Client-side embedding generation — PRD-CORE-033.

Generates 384-dimensional embeddings using sentence-transformers
``all-MiniLM-L6-v2`` for cross-project knowledge sharing via pgvector
semantic search. Graceful degradation when sentence-transformers is not
installed (optional ``[ai]`` extra).

The model is loaded lazily on first call to :func:`embed` and cached
as a module-level singleton. Subsequent calls reuse the loaded model.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

# Module-level model cache
_model: object | None = None
_EMBEDDING_DIM = 384
_MODEL_NAME = "all-MiniLM-L6-v2"


def _load_model() -> object | None:
    """Load the sentence-transformers model, returning None on failure."""
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_MODEL_NAME)
        return _model
    except ImportError:
        logger.debug("sentence_transformers_not_installed")
        return None
    except Exception:
        logger.warning("embedding_model_load_failed", model_name=_MODEL_NAME)
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
    except Exception:
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
    except Exception:
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
