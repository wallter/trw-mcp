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

import math
import re
from datetime import datetime

import structlog
from trw_memory.exceptions import MemoryError as TRWMemoryError
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
    """Search for entries matching keyword tokens (union with IDF-weighted ranking).

    Entries matching more *informative* tokens rank higher: each token's
    contribution is weighted by its inverse document frequency over the
    candidate set, so a discriminating term (matched by few entries) outweighs a
    high-frequency filler token (matched as a substring by most entries). Falls
    back gracefully when some tokens match nothing (unlike strict AND which
    returns empty on any miss). Closes the PRD-DIST-254 MCP-path Recall@5 gap
    where stopword substrings ("I"/"for") let topically-irrelevant entries
    outrank the on-topic record — federation-neutral (no namespace/pool change).

    ``namespace`` defaults to the project namespace; pass ``None`` to search all
    namespaces in a backend (used to query the user-tier store, whose entries
    live under ``user:<id>`` -- PRD-CORE-185 FR06).
    """
    entry_map: dict[str, MemoryEntry] = {}
    # Per-token postings: token -> set of entry ids it matched. The set size is
    # the token's document frequency over the candidate set, which drives IDF.
    token_postings: dict[str, set[str]] = {}

    for token in kw_tokens:
        token_results = backend.search(
            token,
            top_k=top_k,
            tags=tags,
            status=mem_status,
            min_importance=min_impact,
            namespace=namespace,
        )
        matched_ids: set[str] = set()
        for e in token_results:
            if e.id not in entry_map:
                entry_map[e.id] = e
            matched_ids.add(e.id)
        token_postings[token] = matched_ids

    if not entry_map:
        return []

    # IDF over the candidate set: tokens matched by FEW entries are
    # discriminating (high weight); tokens matched by MANY entries (stopword
    # substrings like "I"/"for") are uninformative (low weight). This mirrors
    # the BM25 IDF term the MemoryClient path uses, so the LIKE-substring MCP
    # path stops letting filler-token matches outrank the on-topic record.
    n_candidates = len(entry_map)
    scores: dict[str, float] = dict.fromkeys(entry_map, 0.0)
    for matched_ids in token_postings.values():
        df = len(matched_ids)
        if df == 0:
            continue
        # smoothed IDF, always > 0 so every genuine match still contributes.
        weight = math.log((n_candidates + 1) / (df + 1)) + 1.0
        for eid in matched_ids:
            scores[eid] += weight

    # Sort by IDF-weighted score desc; tie-break on importance then id for a
    # deterministic order across processes.
    ranked_ids = sorted(
        scores,
        key=lambda eid: (scores[eid], entry_map[eid].importance, eid),
        reverse=True,
    )
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
    as_of: datetime | None = None,
    include_superseded: bool = False,
) -> list[MemoryEntry]:
    """Search entries using hybrid (BM25 + vector RRF) or keyword fallback.

    When an embedder is available this delegates to the SAME
    ``trw_memory.retrieval.pipeline.hybrid_search`` (BM25 + dense + RRF with
    importance blend) the ``MemoryClient.recall`` path uses, ranking the full
    candidate pool (``hybrid_search_candidate_pool_size`` entries). Otherwise it
    falls back to the multi-token intersection keyword search.

    PRD-DIST-254 §FR03 follow-up (2026-06-10): the previous hybrid branch
    hand-rolled a divergent fusion -- it ranked only the ≤``top_k``
    LIKE-substring keyword hits + ``hybrid_vector_candidates`` vector hits, and
    fused a LIKE keyword ranking (near-noise on a natural-language query) against
    the vector ranking with pure-position RRF. On the 226-record operator gold
    set this collapsed embeddings-ON Recall@5 to 0.583 (vs MemoryClient 0.9375):
    a gold record at vector rank 0 was demoted to fused rank 5-7 because ~10 junk
    LIKE hits leapfrogged it. Routing through ``hybrid_search`` (BM25 down-weights
    high-frequency filler tokens; the pool spans the whole namespace) closes the
    gap to parity. The fusion logic now lives in exactly one place (DRY), so the
    two paths can no longer drift.

    Cycle 148: ``allow_cold_embedding_init`` (default True for backward
    compat) routes between :func:`get_embedder` (may trigger cold model
    load on first call) and :func:`get_initialized_embedder` (skips cold
    init). The MCP hot path (`recall_factories`, `_session_recall_phase`)
    passes ``False`` to avoid latency spikes; the trw-distill connector
    path passes ``True`` so the canary fixture's first vector recall
    triggers the embed step. Closes the cycle-147 cross-package API
    mismatch that broke 3 tests in tests/eval/test_retrieval_connector.py.

    ``namespace`` defaults to the project namespace; pass ``None`` to rank
    across all namespaces in a backend (user-tier federation, PRD-CORE-185 FR06).
    """

    # Keyword path is always available as the graceful-degradation fallback
    # (no embedder, no vector hits, BM25 absent, or any hybrid error).
    def _keyword_fallback() -> list[MemoryEntry]:
        return _keyword_search(
            backend,
            query,
            top_k=top_k,
            tags=tags,
            mem_status=mem_status,
            min_impact=min_impact,
            namespace=namespace,
        )

    # Route between cold-init and skip-cold-init embedder variants based on the
    # caller's tolerance for the hot model-load latency.
    if allow_cold_embedding_init:
        from trw_mcp.state._memory_connection import get_embedder

        embedder = get_embedder()
    else:
        from trw_mcp.state._memory_connection import get_initialized_embedder

        embedder = get_initialized_embedder()
    if embedder is None:
        return _keyword_fallback()

    try:
        from trw_memory.retrieval.pipeline import hybrid_search

        from trw_mcp.models.config import get_config

        cfg = get_config()

        # Widen the candidate pool to the whole namespace (capped) so BM25 +
        # dense can rank every entry -- matching the MemoryClient pool. Apply the
        # status/min_impact filters at the DB level; the tag filter is applied
        # after ranking (mirrors the MemoryClient post-rank tag narrow).
        candidate_pool_size = max(top_k * 5, cfg.hybrid_search_candidate_pool_size)
        all_entries = backend.list_entries(
            status=mem_status,
            namespace=namespace,
            min_importance=min_impact,
            limit=candidate_pool_size,
        )
        if not all_entries:
            return _keyword_fallback()

        query_vec = embedder.embed(query)
        if query_vec is None:
            return _keyword_fallback()
        stored_embeddings = backend.get_stored_embeddings([e.id for e in all_entries])

        # Auto-scale BM25/vector candidate caps to namespace size so the
        # configured 50-defaults act as FLOORS not CEILINGS (MemoryClient parity).
        namespace_size = len(all_entries)
        effective_bm25 = max(cfg.hybrid_bm25_candidates, namespace_size)
        effective_vector = max(cfg.hybrid_vector_candidates, namespace_size)

        ranked = hybrid_search(
            query=query,
            entries=all_entries,
            embedder=embedder,
            query_embedding=query_vec,
            stored_embeddings=stored_embeddings or None,
            bm25_candidates=effective_bm25,
            vector_candidates=effective_vector,
            rrf_k=cfg.hybrid_rrf_k,
            # F15 / R-FUSION-001: blend learning importance into the position-only
            # RRF score (alpha=0.7 default), mirroring the MemoryClient path.
            importance_alpha=cfg.hybrid_rrf_importance_alpha,
            top_k=top_k if not tags else max(top_k, namespace_size),
            # PRD-CORE-194 FR03: thread the bi-temporal validity prior into the
            # SAME hybrid pass so superseded records are excluded (or, with
            # ``as_of`` / ``include_superseded``, time-travelled) BEFORE the top_k
            # cut -- otherwise the prior could never re-include a record the hybrid
            # pass had already dropped.
            as_of=as_of,
            include_superseded=include_superseded,
        )
        if not ranked:
            return _keyword_fallback()

        if tags:
            tag_set = set(tags)
            ranked = [e for e in ranked if tag_set.issubset(set(e.tags))]

        logger.debug(
            "hybrid_recall_complete",
            namespace_size=namespace_size,
            candidate_pool=candidate_pool_size,
            fused=len(ranked),
        )
        return ranked[:top_k]

    except (OSError, ValueError, RuntimeError, ImportError, TypeError, TRWMemoryError):
        # Hardening (verifier note, 2026-06-10): the original tuple missed two
        # real failure modes that would let an exception ESCAPE this hybrid path
        # and crash recall instead of degrading to keyword:
        #   - ``trw_memory.exceptions.MemoryError`` (TRWMemoryError) family, incl.
        #     ``LocalOnlyViolationError`` raised by the local embedder when network
        #     access is blocked, and ``DimensionMismatchError`` from upsert/search.
        #   - ``TypeError`` from a misconfigured embedder returning a non-vector or
        #     an upstream signature mismatch inside ``hybrid_search``.
        # Recall must always survive to the keyword fallback.
        logger.debug("hybrid_search_failed_fallback_to_keyword", query=query[:80])
        return _keyword_fallback()
