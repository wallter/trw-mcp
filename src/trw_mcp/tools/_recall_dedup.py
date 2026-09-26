"""Post-rank near-duplicate dedup for recall results.

Belongs to the ``_recall_impl.py`` facade. Re-exported there for back-compat.

Recall ranking can surface N near-identical copies of one finding (observed:
25 copies of a single retrospective filling the entire top-K). This module
collapses duplicates AFTER ranking but BEFORE the ``max_results`` truncation so
the highest-ranked representative survives and distinct findings get the slots.

Two passes, both deterministic and cheap on the small post-rank candidate set:

1. Exact-content collapse — entries whose ``(content, detail, summary)`` tuple
   matches an earlier (higher-ranked) entry are dropped. O(K).
2. Optional cosine collapse — when the daemon has an embedder, entries whose
   stored vector is at or above its calibrated threshold against a surviving
   representative are dropped. Vectors and threshold come from one
   ``memory_vectors`` answer (PRD-CORE-302 C2). O(K^2) on the candidate set only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.state._store_selection import VectorSet

logger = structlog.get_logger(__name__)


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
    vectors_fn: Callable[[list[str]], VectorSet | None] | None = None,
) -> tuple[list[dict[str, object]], int]:
    """Collapse near-duplicate entries, keeping the highest-ranked representative.

    Args:
        ranked_learnings: Entries already sorted best-first by the ranker.
        vectors_fn: Optional callable mapping entry IDs to their stored vectors
            and the collapse threshold for that space (``None``: no embedder).
            When provided, a cosine pass runs after exact collapse.

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
    if vectors_fn is not None and len(survivors) > 1:
        survivors = _cosine_collapse(survivors, vectors_fn)

    collapsed = original_count - len(survivors)
    if collapsed:
        logger.debug("recall_dedup_collapsed", removed=collapsed, kept=len(survivors))
    return survivors, collapsed


def _cosine_collapse(
    survivors: list[dict[str, object]],
    vectors_fn: Callable[[list[str]], VectorSet | None],
) -> list[dict[str, object]]:
    """Drop entries whose vector is a near-duplicate of an earlier survivor's."""
    ids = [str(e.get("id", "")) for e in survivors]
    try:
        vector_set = vectors_fn([i for i in ids if i])
    except Exception:  # justified: fail-open, embedding lookup must not block recall
        logger.debug("recall_dedup_embedding_lookup_failed", exc_info=True)
        return survivors

    if vector_set is None or not vector_set.vectors:
        return survivors
    embeddings, cosine_threshold = vector_set

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
