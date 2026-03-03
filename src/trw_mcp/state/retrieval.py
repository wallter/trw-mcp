"""Hybrid retrieval engine: BM25 + dense vectors + Reciprocal Rank Fusion.

Combines sparse (BM25) and dense (embedding) retrieval with RRF fusion
for learning entry search. Falls back gracefully when components are
unavailable.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileStateReader

try:
    from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]
    _BM25_AVAILABLE = True
except ImportError:
    _BM25_AVAILABLE = False

logger = structlog.get_logger()


def bm25_search(
    query: str,
    entries: list[dict[str, object]],
    top_k: int = 50,
) -> list[tuple[str, float]]:
    """Run BM25 sparse retrieval over a list of learning entries.

    Args:
        query: The search query string.
        entries: List of learning entry dicts. Each must have 'id', 'summary',
            'detail', and 'tags' keys.
        top_k: Maximum number of results to return.

    Returns:
        List of (entry_id, score) pairs sorted by score descending.
        Empty list when rank_bm25 is unavailable or entries is empty.
    """
    if not _BM25_AVAILABLE or not entries:
        return []

    # Build tokenized corpus: summary + detail + tags
    # Expand hyphenated tags so "pydantic-v2" also matches query token "pydantic"
    corpus: list[list[str]] = []
    for entry in entries:
        summary = str(entry.get("summary", "")).lower()
        detail = str(entry.get("detail", "")).lower()
        raw_tags = entry.get("tags", [])
        tag_parts: list[str] = []
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                tag_str = str(tag).lower()
                tag_parts.append(tag_str)
                if "-" in tag_str:
                    tag_parts.extend(tag_str.split("-"))
        tags_str = " ".join(tag_parts)
        text = f"{summary} {detail} {tags_str}"
        corpus.append(text.split())

    tokenized_query = query.lower().split()
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(tokenized_query)

    # Build (entry_id, score) pairs
    paired: list[tuple[str, float]] = []
    for i, entry in enumerate(entries):
        score = float(scores[i])
        entry_id = str(entry.get("id", ""))
        if entry_id:
            paired.append((entry_id, score))

    # BM25 IDF is 0 when a term appears in exactly N/2 documents (small corpora).
    # Fall back to token-overlap scoring when all BM25 scores are zero.
    if all(s == 0.0 for _, s in paired):
        query_set = set(tokenized_query)
        fallback: list[tuple[str, float]] = []
        for i, entry in enumerate(entries):
            entry_id = str(entry.get("id", ""))
            if not entry_id:
                continue
            overlap = len(query_set & set(corpus[i]))
            if overlap > 0:
                fallback.append((entry_id, float(overlap)))
        fallback.sort(key=lambda x: x[1], reverse=True)
        return fallback[:top_k]

    paired.sort(key=lambda x: x[1], reverse=True)
    # Only return entries with positive score to avoid noise
    return [(eid, s) for eid, s in paired if s > 0.0][:top_k]


def rrf_fuse(
    rankings: list[list[tuple[str, float]]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of multiple ranked result lists.

    Implements the RRF formula from Cormack et al. (2009):
        score(d) = Σ 1 / (k + rank_i(d))
    where rank_i is the 1-based rank of document d in ranking list i.

    Args:
        rankings: List of ranked result lists. Each inner list is a sequence
            of (entry_id, score) pairs sorted by relevance descending.
        k: RRF constant. Default 60 (from original paper).

    Returns:
        Fused list of (entry_id, rrf_score) pairs sorted by RRF score descending.
        Empty list when rankings is empty.
    """
    if not rankings:
        return []

    fused_scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, (entry_id, _) in enumerate(ranking):
            fused_scores[entry_id] = fused_scores.get(entry_id, 0.0) + 1.0 / (k + rank + 1)

    result = list(fused_scores.items())
    result.sort(key=lambda x: x[1], reverse=True)
    return result


def hybrid_search(
    query: str,
    entries_dir: Path,
    reader: FileStateReader,
    *,
    top_k: int = 25,
    config: object | None = None,
) -> list[dict[str, object]]:
    """Hybrid BM25 + vector search with RRF fusion.

    Main entry point called by recall_search.py. Combines sparse (BM25)
    and dense (sqlite-vec) retrieval via Reciprocal Rank Fusion.

    Graceful degradation:
    - If sqlite-vec unavailable → BM25-only
    - If rank_bm25 unavailable → empty results
    - If entries_dir does not exist → empty results

    Args:
        query: The search query string.
        entries_dir: Path to the learnings/entries/ directory.
        reader: File state reader instance.
        top_k: Maximum number of results to return.
        config: Optional TRWConfig instance for tuning parameters.

    Returns:
        List of matching learning entry dicts sorted by relevance descending.
    """
    if not entries_dir.exists():
        return []

    _t0 = time.monotonic()

    # Resolve config params
    bm25_candidates = 50
    vector_candidates = 50
    rrf_k = 60

    if config is not None:
        bm25_candidates = int(getattr(config, "hybrid_bm25_candidates", 50))
        vector_candidates = int(getattr(config, "hybrid_vector_candidates", 50))
        rrf_k = int(getattr(config, "hybrid_rrf_k", 60))

    # Load all active entries
    all_entries: list[dict[str, object]] = []
    entry_map: dict[str, dict[str, object]] = {}
    for entry_file in sorted(entries_dir.glob("*.yaml")):
        try:
            data: dict[str, object] = reader.read_yaml(entry_file)
            entry_id = str(data.get("id", ""))
            if entry_id:
                all_entries.append(data)
                entry_map[entry_id] = data
        except Exception as exc:
            logger.debug("entry_load_skipped", exc_type=type(exc).__name__, path=str(entry_file))
            continue

    if not all_entries:
        return []

    rankings: list[list[tuple[str, float]]] = []

    # --- BM25 sparse retrieval ---
    bm25_results = bm25_search(query, all_entries, top_k=bm25_candidates)
    if bm25_results:
        rankings.append(bm25_results)

    # --- Dense vector retrieval (optional) ---
    _vector_count = 0
    from trw_mcp.state.memory_store import MemoryStore
    if MemoryStore.available():
        try:
            from trw_mcp.state._paths import resolve_memory_store_path
            from trw_mcp.telemetry.embeddings import embed
            query_embedding = embed(query)
            if query_embedding is not None:
                store_path = resolve_memory_store_path()
                store = MemoryStore(store_path, dim=384)
                try:
                    if store.count() > 0:
                        raw_results = store.search(query_embedding, top_k=vector_candidates)
                        # Convert distances to scores (lower distance = higher score)
                        dense_results: list[tuple[str, float]] = [
                            (entry_id, 1.0 / (1.0 + dist)) for entry_id, dist in raw_results
                        ]
                        if dense_results:
                            _vector_count = len(dense_results)
                            rankings.append(dense_results)
                finally:
                    store.close()
        except Exception as exc:
            logger.debug("vector_store_unavailable", exc_type=type(exc).__name__)

    if not rankings:
        return []

    # --- RRF fusion ---
    fused = rrf_fuse(rankings, k=rrf_k)

    # Collect top_k results preserving fusion order
    results: list[dict[str, object]] = []
    for entry_id, _ in fused[:top_k]:
        if entry_id in entry_map:
            results.append(entry_map[entry_id])

    logger.debug(
        "hybrid_search_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        results=len(results),
        bm25_count=len(bm25_results),
        vector_count=_vector_count,
    )
    return results
