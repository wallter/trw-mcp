"""Post-rank near-duplicate dedup for recall results.

Belongs to the ``_recall_impl.py`` facade. Re-exported there for back-compat.

Recall ranking can surface N near-identical copies of one finding (observed:
25 copies of a single retrospective filling the entire top-K). This module
collapses duplicates AFTER ranking but BEFORE the ``max_results`` truncation so
the highest-ranked representative survives and distinct findings get the slots.

Two passes, both deterministic and cheap on the small post-rank candidate set:

1. Exact-content collapse — entries whose ``(content, detail, summary)`` tuple
   matches an earlier (higher-ranked) entry are dropped. O(K).
2. Optional cosine collapse — when stored embeddings are available, entries
   whose embedding cosine-similarity to a surviving representative exceeds the
   threshold are dropped. O(K^2) on the candidate set only (never the corpus).
"""

from __future__ import annotations

import math
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)

# Default cosine threshold for near-duplicate collapse. Entries at or above this
# similarity to a surviving representative are treated as duplicates.
DEFAULT_COSINE_DUP_THRESHOLD = 0.9


def _content_key(entry: dict[str, object]) -> str:
    """Deterministic identity key for exact-content dedup.

    Uses content + detail + summary so byte-identical findings collapse even
    when one field is empty (compact entries carry only summary).
    """
    content = str(entry.get("content", ""))
    detail = str(entry.get("detail", ""))
    summary = str(entry.get("summary", ""))
    return "\x00".join((content, detail, summary))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for two equal-length vectors; 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def dedup_ranked_learnings(
    ranked_learnings: list[dict[str, object]],
    *,
    embeddings_fn: Callable[[list[str]], dict[str, list[float]]] | None = None,
    cosine_threshold: float = DEFAULT_COSINE_DUP_THRESHOLD,
) -> tuple[list[dict[str, object]], int]:
    """Collapse near-duplicate entries, keeping the highest-ranked representative.

    Args:
        ranked_learnings: Entries already sorted best-first by the ranker.
        embeddings_fn: Optional callable mapping entry IDs to stored embedding
            vectors. When provided, a cosine pass runs after exact collapse.
        cosine_threshold: Similarity at/above which two entries are duplicates.

    Returns:
        ``(deduped_entries, collapsed_count)`` where ``collapsed_count`` is the
        number of entries removed. Order of survivors is preserved.
    """
    if len(ranked_learnings) <= 1:
        return ranked_learnings, 0

    original_count = len(ranked_learnings)

    # Pass 1 — exact content collapse (O(K)).
    seen_keys: set[str] = set()
    survivors: list[dict[str, object]] = []
    for entry in ranked_learnings:
        key = _content_key(entry)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        survivors.append(entry)

    # Pass 2 — cosine collapse on stored embeddings (O(K^2), candidate set only).
    if embeddings_fn is not None and len(survivors) > 1:
        survivors = _cosine_collapse(survivors, embeddings_fn, cosine_threshold)

    collapsed = original_count - len(survivors)
    if collapsed:
        logger.debug("recall_dedup_collapsed", removed=collapsed, kept=len(survivors))
    return survivors, collapsed


def _cosine_collapse(
    survivors: list[dict[str, object]],
    embeddings_fn: Callable[[list[str]], dict[str, list[float]]],
    cosine_threshold: float,
) -> list[dict[str, object]]:
    """Drop entries whose embedding is near-duplicate of an earlier survivor."""
    ids = [str(e.get("id", "")) for e in survivors]
    try:
        embeddings = embeddings_fn([i for i in ids if i])
    except Exception:  # justified: fail-open, embedding lookup must not block recall
        logger.debug("recall_dedup_embedding_lookup_failed", exc_info=True)
        return survivors

    if not embeddings:
        return survivors

    kept: list[dict[str, object]] = []
    kept_vectors: list[list[float]] = []
    for entry in survivors:
        vec = embeddings.get(str(entry.get("id", "")))
        if not vec:
            kept.append(entry)
            continue
        if any(_cosine(vec, prior) >= cosine_threshold for prior in kept_vectors):
            continue
        kept.append(entry)
        kept_vectors.append(vec)
    return kept
