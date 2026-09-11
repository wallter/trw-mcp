"""One query-relative lexical/dense fusion over the admitted candidate pool.

No utility priors, persistent score fields, or per-store rank comparisons.
"""

from __future__ import annotations

from trw_memory.models.memory import MemoryEntry
from trw_memory.retrieval.bm25 import bm25_search
from trw_memory.retrieval.fusion import rrf_fuse


def query_relevance(matches: list[dict[str, object]], query_tokens: list[str]) -> list[float]:
    """Return aligned relevance in [0, 1], using only request-bound dense data.

    RRF is normalized by its theoretical bound, streams/(k+1), not the best
    candidate in an independently scored store. Equal source scores tie.
    """
    from trw_mcp.state._recall_signals import current_recall_signals

    query = " ".join(query_tokens).lower()
    query_tokens = query.split()
    # Positions are request-local IDs: colliding persisted IDs cannot alias the
    # lexical corpus or evidence, including across namespace/source boundaries.
    entries = [
        MemoryEntry(
            id=str(index),
            content=str(row.get("summary", "")),
            detail=str(row.get("detail", "")),
            tags=[str(tag) for tag in tags] if isinstance(tags := row.get("tags"), list) else [],
        )
        for index, row in enumerate(matches)
    ]
    lexical = bm25_search(query, entries, top_k=len(entries))
    if not lexical:
        # Dependency-free fallback for an unavailable BM25 dependency or empty
        # lexical ranking. Evaluate the same admitted pool, without field priors.
        tokens = set(query_tokens)
        lexical = [
            (
                entry.id,
                float(
                    sum(token in f"{entry.content} {entry.detail} {' '.join(entry.tags)}".lower() for token in tokens)
                ),
            )
            for entry in entries
        ]
        lexical = sorted((pair for pair in lexical if pair[1] > 0), key=lambda pair: pair[1], reverse=True)

    signals = current_recall_signals()
    observed = (
        [(str(i), signals.get(row)) for i, row in enumerate(matches)]
        if signals is not None and signals.query.lower().split() == query_tokens
        else []
    )
    spaces = {signal.embedding_space for _, signal in observed if signal is not None}
    # Different embedding spaces are incomparable, not extra supporting votes.
    # Conservatively degrade to lexical instead of silently combining them.
    dense = (
        sorted(
            [(key, signal.cosine) for key, signal in observed if signal is not None and signal.cosine > 0],
            key=lambda pair: pair[1],
            reverse=True,
        )
        # Admission already checked each stored generation record against the
        # query descriptor. Matching spaces are comparable across stores too.
        if len(spaces) == 1
        else []
    )
    rankings = [ranking for ranking in (lexical, dense) if ranking]
    if not rankings:
        return [0.0] * len(matches)
    from trw_mcp.scoring._utils import get_config

    k = get_config().hybrid_rrf_k
    if k < 1:
        k = 5  # same invalid-setting fallback as the shared fusion primitive
    bound = len(rankings) / (k + 1)
    fused = dict(rrf_fuse(rankings, k=k, alpha=1.0, tie_scores=True))
    return [min(1.0, fused.get(str(i), 0.0) / bound) for i in range(len(matches))]
