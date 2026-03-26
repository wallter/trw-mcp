"""Importance scoring for tiered memory (extracted from tiers.py).

Implements FR05 — composite importance score used for tier transitions
and recall ranking.

Parent facade: ``trw_mcp.state.tiers`` (re-exports ``compute_importance_score``).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.scoring import _days_since_access
from trw_mcp.state.dedup import cosine_similarity


def compute_importance_score(
    entry: dict[str, object],
    query_tokens: list[str],
    query_embedding: list[float] | None = None,
    entry_embedding: list[float] | None = None,
    *,
    config: TRWConfig | None = None,
) -> float:
    """Compute a composite importance score for a learning entry.

    Formula: score = w1*relevance + w2*recency + w3*importance

    Weights are normalized if they don't sum to 1.0.

    Args:
        entry: Learning entry as a dict (from YAML).
        query_tokens: Tokenized query for token-overlap fallback.
        query_embedding: Optional dense query vector for cosine similarity.
        entry_embedding: Optional dense entry vector for cosine similarity.
        config: TRWConfig for weights and decay settings. Uses get_config() if None.

    Returns:
        Composite importance score in [0.0, 1.0].
    """
    cfg = config or get_config()

    w1 = cfg.memory_score_w1
    w2 = cfg.memory_score_w2
    w3 = cfg.memory_score_w3

    # Normalize weights
    total_w = w1 + w2 + w3
    if total_w > 0 and abs(total_w - 1.0) > 1e-9:
        w1 /= total_w
        w2 /= total_w
        w3 /= total_w

    # Relevance: cosine similarity when both embeddings present, else token overlap
    if query_embedding is not None and entry_embedding is not None:
        relevance = max(0.0, cosine_similarity(query_embedding, entry_embedding))
    else:
        # Token overlap ratio fallback
        entry_text = str(entry.get("summary", "")).lower() + " " + str(entry.get("detail", "")).lower()
        entry_tokens = set(entry_text.split())
        query_set = {t.lower() for t in query_tokens}
        if query_set:
            relevance = len(query_set & entry_tokens) / len(query_set)
        else:
            relevance = 0.0

    # Recency: exponential decay based on days since access
    today = datetime.now(tz=timezone.utc).date()
    days = _days_since_access(entry, today)
    half_life = cfg.learning_decay_half_life_days
    decay_rate = math.log(2) / half_life if half_life > 0 else 0.0
    recency = math.exp(-decay_rate * days)

    # Importance: the entry's Bayesian-calibrated impact field
    importance = float(str(entry.get("impact", 0.5)))
    importance = max(0.0, min(1.0, importance))

    score = w1 * relevance + w2 * recency + w3 * importance
    return max(0.0, min(1.0, score))
