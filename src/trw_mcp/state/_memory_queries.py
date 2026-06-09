"""Query construction and routing for memory search operations.

Handles keyword search (single-token, multi-token intersection), learning-ID
direct lookup, and hybrid search (keyword + vector RRF fusion).

This module is an internal implementation detail of ``memory_adapter.py``.
External code should import from ``memory_adapter`` (the public facade).

Imports ``get_embedder`` from ``_memory_connection`` (its definition site) and
``get_config`` from ``trw_mcp.models.config`` to avoid circular dependencies
through the facade.
"""

from __future__ import annotations

import re

import structlog
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state._constants import DEFAULT_NAMESPACE

logger = structlog.get_logger(__name__)

_NAMESPACE = DEFAULT_NAMESPACE
_LEARNING_ID_RE = re.compile(r"^L-[0-9a-zA-Z]{4,}$")


def _apply_entry_filters(
    entry: MemoryEntry,
    tags: list[str] | None,
    mem_status: MemoryStatus | None,
    min_impact: float,
) -> bool:
    """Check if an entry passes all filter criteria.

    Returns True if entry should be included, False otherwise.
    """
    if min_impact > 0.0 and entry.importance < min_impact:
        return False
    if mem_status is not None and entry.status != mem_status:
        return False
    return not (tags and not set(tags).issubset(set(entry.tags)))


def _lookup_id_tokens(
    backend: SQLiteBackend,
    id_tokens: list[str],
    tags: list[str] | None,
    mem_status: MemoryStatus | None,
    min_impact: float,
) -> tuple[list[MemoryEntry], set[str]]:
    """Direct lookup for learning ID tokens (OR semantics).

    Returns (id_entries, seen_ids).
    """
    seen_ids: set[str] = set()
    id_entries: list[MemoryEntry] = []
    for lid in id_tokens:
        entry = backend.get(lid)
        if entry is not None and entry.id not in seen_ids and _apply_entry_filters(entry, tags, mem_status, min_impact):
            id_entries.append(entry)
            seen_ids.add(entry.id)
    return id_entries, seen_ids


def _search_intersect_keywords(
    backend: SQLiteBackend,
    kw_tokens: list[str],
    top_k: int,
    tags: list[str] | None,
    mem_status: MemoryStatus | None,
    min_impact: float,
    namespace: str | None = _NAMESPACE,
) -> list[MemoryEntry]:
    """Search for entries matching keyword tokens (union with match-count ranking).

    Entries matching more tokens rank higher.  Falls back gracefully when some
    tokens match nothing (unlike strict AND which returns empty on any miss).

    ``namespace`` defaults to the project namespace; pass ``None`` to search all
    namespaces in a backend (used to query the user-tier store, whose entries
    live under ``user:<id>`` -- PRD-CORE-185 FR06).
    """
    entry_map: dict[str, MemoryEntry] = {}
    match_counts: dict[str, int] = {}

    for token in kw_tokens:
        token_results = backend.search(
            token,
            top_k=top_k,
            tags=tags,
            status=mem_status,
            min_importance=min_impact,
            namespace=namespace,
        )
        for e in token_results:
            if e.id not in entry_map:
                entry_map[e.id] = e
                match_counts[e.id] = 0
            match_counts[e.id] += 1

    if not entry_map:
        return []

    # Sort by match count descending (most tokens matched first)
    ranked_ids = sorted(match_counts, key=lambda eid: match_counts[eid], reverse=True)
    return [entry_map[eid] for eid in ranked_ids[:top_k]]


def _keyword_search(
    backend: SQLiteBackend,
    query: str,
    *,
    top_k: int = 25,
    tags: list[str] | None = None,
    mem_status: MemoryStatus | None = None,
    min_impact: float = 0.0,
    namespace: str | None = _NAMESPACE,
) -> list[MemoryEntry]:
    """Multi-token keyword search with learning-ID direct lookup.

    Tokens matching the ``L-[0-9a-f]{8}`` pattern are resolved via direct
    ``backend.get()`` (O(1) primary-key lookup).  Remaining keyword tokens
    use intersection search (entry must match ALL keyword tokens).  The two
    result sets are unioned (IDs first, then keyword matches) and deduped.

    ``namespace`` defaults to the project namespace; pass ``None`` to search all
    namespaces in a backend (user-tier federation, PRD-CORE-185 FR06).
    """
    tokens = query.split()
    if len(tokens) <= 1:
        # Single token -- check if it's a learning ID for direct lookup
        if tokens and _LEARNING_ID_RE.match(tokens[0]):
            entry = backend.get(tokens[0])
            if entry is None:
                return []
            if _apply_entry_filters(entry, tags, mem_status, min_impact):
                return [entry]
            return []
        return backend.search(
            query,
            top_k=top_k,
            tags=tags,
            status=mem_status,
            min_importance=min_impact,
            namespace=namespace,
        )

    # Partition tokens into learning IDs and keyword terms
    id_tokens: list[str] = []
    kw_tokens: list[str] = []
    for t in tokens:
        if _LEARNING_ID_RE.match(t):
            id_tokens.append(t)
        else:
            kw_tokens.append(t)

    id_entries, seen_ids = _lookup_id_tokens(backend, id_tokens, tags, mem_status, min_impact)

    # Keyword search for remaining tokens (AND/intersection semantics)
    kw_entries: list[MemoryEntry] = []
    if kw_tokens:
        if len(kw_tokens) == 1:
            kw_entries = backend.search(
                kw_tokens[0],
                top_k=top_k,
                tags=tags,
                status=mem_status,
                min_importance=min_impact,
                namespace=namespace,
            )
        else:
            kw_entries = _search_intersect_keywords(
                backend,
                kw_tokens,
                top_k,
                tags,
                mem_status,
                min_impact,
                namespace=namespace,
            )

    # Union: ID lookups first, then keyword results (deduped)
    results: list[MemoryEntry] = list(id_entries)
    for e in kw_entries:
        if e.id not in seen_ids:
            results.append(e)
            seen_ids.add(e.id)

    return results[:top_k]


def _search_entries(
    backend: SQLiteBackend,
    query: str,
    *,
    top_k: int = 25,
    tags: list[str] | None = None,
    mem_status: MemoryStatus | None = None,
    min_impact: float = 0.0,
    allow_cold_embedding_init: bool = True,
    namespace: str | None = _NAMESPACE,
) -> list[MemoryEntry]:
    """Search entries using hybrid (keyword + vector RRF) or keyword fallback.

    When embedder is available, runs keyword search and sqlite-vec vector search
    in parallel, then fuses via Reciprocal Rank Fusion. Otherwise falls back to
    multi-token intersection keyword search.

    Cycle 148: ``allow_cold_embedding_init`` (default True for backward
    compat) routes between :func:`get_embedder` (may trigger cold model
    load on first call) and :func:`get_initialized_embedder` (skips cold
    init). The MCP hot path (`recall_factories`, `_session_recall_phase`)
    passes ``False`` to avoid latency spikes; the trw-distill connector
    path passes ``True`` so the canary fixture's first vector recall
    triggers the embed step. Closes the cycle-147 cross-package API
    mismatch that broke 3 tests in tests/eval/test_retrieval_connector.py.
    """
    # Always run keyword search
    keyword_results = _keyword_search(
        backend,
        query,
        top_k=top_k,
        tags=tags,
        mem_status=mem_status,
        min_impact=min_impact,
        namespace=namespace,
    )

    # Try vector search when embedder is available. Route between cold-init
    # and skip-cold-init variants based on caller's tolerance for the hot
    # model-load latency.
    if allow_cold_embedding_init:
        from trw_mcp.state._memory_connection import get_embedder

        embedder = get_embedder()
    else:
        from trw_mcp.state._memory_connection import get_initialized_embedder

        embedder = get_initialized_embedder()
    if embedder is None:
        return keyword_results

    try:
        query_vec = embedder.embed(query)
        if query_vec is None:
            return keyword_results

        from trw_mcp.models.config import get_config

        cfg = get_config()
        vector_hits = backend.search_vectors(query_vec, top_k=cfg.hybrid_vector_candidates)
        if not vector_hits:
            return keyword_results

        # RRF fusion: merge keyword and vector rankings
        keyword_ranking = [(e.id, 1.0 / (i + 1)) for i, e in enumerate(keyword_results)]
        vector_ranking = [(eid, score) for eid, score in vector_hits]

        from trw_memory.retrieval.fusion import rrf_fuse

        # Build id->entry map from keyword results + vector-matched entries
        # FIRST so the importances mapping below can read each candidate's
        # impact/importance.
        entry_map: dict[str, MemoryEntry] = {e.id: e for e in keyword_results}
        # Fetch any vector-only hits not already in keyword results
        for eid, _ in vector_hits:
            if eid not in entry_map:
                entry = backend.get(eid)
                if entry is not None and _apply_entry_filters(entry, tags, mem_status, min_impact):
                    entry_map[eid] = entry

        # F15 / R-FUSION-001: blend learning importance into the position-only
        # RRF score, mirroring the MemoryClient path
        # (trw_memory.retrieval.pipeline.hybrid_search). Pure-position fusion
        # ignores impact, so impact-0.95 tribal knowledge tied at the same rank
        # as impact-0.2 noise. Only pass importances when alpha < 1.0; alpha=1.0
        # keeps the legacy pure-position behaviour bit-for-bit (back-compat).
        importance_alpha = cfg.hybrid_rrf_importance_alpha
        importances: dict[str, float] | None = (
            {eid: e.importance for eid, e in entry_map.items()} if importance_alpha < 1.0 else None
        )

        fused = rrf_fuse(
            [keyword_ranking, vector_ranking],
            k=cfg.hybrid_rrf_k,
            importances=importances,
            alpha=importance_alpha,
        )

        results: list[MemoryEntry] = []
        for eid, _ in fused[:top_k]:
            if eid in entry_map:
                results.append(entry_map[eid])

        logger.debug(
            "hybrid_recall_complete",
            keyword_hits=len(keyword_results),
            vector_hits=len(vector_hits),
            fused=len(results),
        )
        return results

    except (OSError, ValueError, RuntimeError):
        logger.debug("vector_search_failed_fallback_to_keyword", query=query[:80])
        return keyword_results
