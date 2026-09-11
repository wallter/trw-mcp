"""Query construction and routing for memory search operations.

Handles keyword search (single-token, IDF-weighted multi-token union), learning-ID
direct lookup, and hybrid search (keyword + vector RRF fusion).

This module is an internal implementation detail of ``memory_adapter.py``.
External code should import from ``memory_adapter`` (the public facade).

Imports ``get_embedder`` from ``_memory_connection`` (its definition site) and
``get_config`` from ``trw_mcp.models.config`` to avoid circular dependencies
through the facade.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime

import structlog
from trw_memory.exceptions import MemoryError as TRWMemoryError
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.retrieval.temporal_selection import TemporalSelection
from trw_memory.security.namespace_scope import authorize_namespaces
from trw_memory.security.rbac import Permission
from trw_memory.storage.sqlite_backend import SQLiteBackend
from typing_extensions import TypedDict

from trw_mcp.state._backend_id_lookup import resolve_entry_in_backend
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state._recall_signals import current_recall_signals


class _TemporalSearchOptions(TypedDict, total=False):
    temporal_selection: TemporalSelection


class _DenseSearchOptions(TypedDict, total=False):
    dense_observer: Callable[[tuple[tuple[str, float], ...]], None]


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
    namespace: str | None,
) -> tuple[list[MemoryEntry], set[str]]:
    """Direct lookup for learning ID tokens (OR semantics).

    ``namespace`` is the scope the search is running in; ``None`` means the
    federated "any namespace in this store" case (PRD-CORE-185 FR06).

    Returns (id_entries, seen_ids).
    """
    seen_ids: set[str] = set()
    id_entries: list[MemoryEntry] = []
    for lid in id_tokens:
        entry = resolve_entry_in_backend(backend, lid, namespace=namespace)
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
    temporal_selection: TemporalSelection | None = None,
) -> list[MemoryEntry]:
    """Search for entries matching keyword tokens (union with IDF-weighted ranking).

    Entries matching more *informative* tokens rank higher: each token's
    contribution is weighted by its inverse document frequency over the
    SQL-filtered matching union before top_k (not the temporal-eligible subset),
    so a discriminating term (matched by few entries) outweighs a
    high-frequency filler token (matched as a substring by most entries). Falls
    back gracefully when some tokens match nothing (unlike strict AND which
    returns empty on any miss). Closes the PRD-DIST-254 MCP-path Recall@5 gap
    where stopword substrings ("I"/"for") let topically-irrelevant entries
    outrank the on-topic record — federation-neutral (no namespace/pool change).

    ``namespace`` defaults to the project namespace; pass ``None`` to search all
    namespaces in a backend (used to query the user-tier store, whose entries
    live under ``user:<id>`` -- PRD-CORE-185 FR06).
    """
    return backend.search(
        " ".join(kw_tokens),
        keyword_tokens=kw_tokens,
        top_k=top_k,
        tags=tags,
        status=mem_status,
        min_importance=min_impact,
        namespace=namespace,
        **(_TemporalSearchOptions(temporal_selection=temporal_selection) if temporal_selection is not None else {}),
    )


def _keyword_search(
    backend: SQLiteBackend,
    query: str,
    *,
    top_k: int = 25,
    tags: list[str] | None = None,
    mem_status: MemoryStatus | None = None,
    min_impact: float = 0.0,
    namespace: str | None = _NAMESPACE,
    temporal_selection: TemporalSelection | None = None,
) -> list[MemoryEntry]:
    """Multi-token keyword search with learning-ID direct lookup.

    Tokens matching the ``L-[0-9a-f]{8}`` pattern are resolved via direct
    ``backend.get()`` (O(1) primary-key lookup).  Remaining keyword tokens
    use IDF-weighted union search (unmatched tokens do not force zero). The two
    result sets are unioned (IDs first, then keyword matches) and deduped.

    ``namespace`` defaults to the project namespace; pass ``None`` to search all
    namespaces in a backend (user-tier federation, PRD-CORE-185 FR06).
    """
    tokens = query.split()
    if len(tokens) <= 1:
        # Single token -- check if it's a learning ID for direct lookup
        if tokens and _LEARNING_ID_RE.match(tokens[0]):
            entry = resolve_entry_in_backend(backend, tokens[0], namespace=namespace)
            if entry is None:
                return []
            if _apply_entry_filters(entry, tags, mem_status, min_impact):
                return temporal_selection.select([entry], limit=top_k) if temporal_selection else [entry]
            return []
        return backend.search(
            query,
            top_k=top_k,
            tags=tags,
            status=mem_status,
            min_importance=min_impact,
            namespace=namespace,
            **(_TemporalSearchOptions(temporal_selection=temporal_selection) if temporal_selection is not None else {}),
        )

    # Partition tokens into learning IDs and keyword terms
    id_tokens: list[str] = []
    kw_tokens: list[str] = []
    for t in tokens:
        if _LEARNING_ID_RE.match(t):
            id_tokens.append(t)
        else:
            kw_tokens.append(t)

    id_entries, seen_ids = _lookup_id_tokens(backend, id_tokens, tags, mem_status, min_impact, namespace)

    # Keyword search for remaining tokens (IDF-weighted union semantics)
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
                **(
                    _TemporalSearchOptions(temporal_selection=temporal_selection)
                    if temporal_selection is not None
                    else {}
                ),
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
                **(
                    _TemporalSearchOptions(temporal_selection=temporal_selection)
                    if temporal_selection is not None
                    else {}
                ),
            )

    # Union: ID lookups first, then keyword results (deduped)
    results: list[MemoryEntry] = list(id_entries)
    for e in kw_entries:
        if e.id not in seen_ids:
            results.append(e)
            seen_ids.add(e.id)

    return temporal_selection.select(results, limit=top_k) if temporal_selection else results[:top_k]


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
    temporal_selection: TemporalSelection | None = None,
) -> list[MemoryEntry]:
    """Search entries using hybrid (BM25 + vector RRF) or keyword fallback.

    When an embedder is available this delegates to the SAME
    ``trw_memory.retrieval.pipeline.hybrid_search`` (BM25 + dense + RRF with
    importance blend) the ``MemoryClient.recall`` path uses, ranking the full
    candidate pool (``hybrid_search_candidate_pool_size`` entries). Otherwise it
    falls back to the IDF-weighted multi-token keyword union.

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

    selection = temporal_selection or TemporalSelection(
        as_of=as_of, include_superseded=include_superseded, exclude_system_canaries=True
    )

    # Keyword path is always available as the graceful-degradation fallback
    # (no embedder, no vector hits, BM25 absent, or any hybrid error). It
    # already resolves learning-ID tokens via direct primary-key lookup +
    # union (see ``_keyword_search``), so the fallback needs no extra handling.
    def _keyword_fallback() -> list[MemoryEntry]:
        return _keyword_search(
            backend,
            query,
            top_k=top_k,
            tags=tags,
            mem_status=mem_status,
            min_impact=min_impact,
            namespace=namespace,
            temporal_selection=selection,
        )

    # FIX-055 parity for the hybrid path: learning-ID tokens (``L-xxxx``) must
    # resolve via direct primary-key lookup and UNION into the ranked results,
    # independent of whether the embeddings-on hybrid ranker or the keyword
    # fallback runs. The hybrid delegation (eb1f0e92c) ranks the natural-language
    # query only, so once embeddings became the default (f4ca661c9) a mixed
    # ``"L-xxxx keyword"`` query silently dropped the ID lookup -- the ranker has
    # no reason to surface an entry whose text does not match the query. Direct
    # lookups are prepended (higher priority than fuzzy hits) and deduped.
    id_tokens = [t for t in query.split() if _LEARNING_ID_RE.match(t)]

    def _union_id_lookups(ranked: list[MemoryEntry]) -> list[MemoryEntry]:
        if not id_tokens:
            return ranked
        id_entries, seen_ids = _lookup_id_tokens(backend, id_tokens, tags, mem_status, min_impact, namespace)
        merged: list[MemoryEntry] = list(id_entries)
        for entry in ranked:
            if entry.id not in seen_ids:
                merged.append(entry)
                seen_ids.add(entry.id)
        return selection.select(merged, limit=top_k)

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
            temporal_selection=selection,
            tags=tags,
        )
        if not all_entries:
            return _keyword_fallback()

        from trw_memory.embeddings.provenance import provider_embedding_space, vector_digest

        # Identity must bracket the actual query encode. A model name, current
        # provider object or a nonempty legacy vector is not generation proof.
        query_space = provider_embedding_space(embedder)
        if query_space is None:
            logger.debug("semantic_recall_unqualified", reason="provider_identity_unknown")
            return _keyword_fallback()
        query_vec = embedder.embed(query)
        if (
            query_vec is None
            or provider_embedding_space(embedder) != query_space
            or len(query_vec) != query_space.dimensions
        ):
            return _keyword_fallback()
        vector_digest(query_vec)  # Reject nonfinite/unrepresentable query evidence.

        # Auto-scale BM25/vector candidate caps to namespace size so the
        # configured 50-defaults act as FLOORS not CEILINGS (MemoryClient parity).
        namespace_size = len(all_entries)
        effective_bm25 = max(cfg.hybrid_bm25_candidates, namespace_size)
        effective_vector = max(cfg.hybrid_vector_candidates, namespace_size)

        # PRD-CORE-245 FR04: the scope is minted from the namespaces the
        # candidate rows actually carry. ``namespace=None`` is the user-tier
        # federation case (PRD-CORE-185 FR06), where the caller deliberately
        # spans stores; going through the authorizer keeps that explicit and
        # still runs the per-namespace permission check.
        scope = authorize_namespaces(
            MemoryConfig(),
            {namespace} if namespace is not None else {e.namespace for e in all_entries},
            Permission.READ,
            "recall",
        )
        # A bare-ID read can borrow another namespace's same-ID vector. Query
        # each authorized candidate namespace explicitly, before any fusion.
        # The shared pipeline addresses vectors by ID, so ambiguous pool IDs
        # cannot safely receive dense evidence even after qualified reads.
        candidate_id_counts = Counter(entry.id for entry in all_entries)
        ids_by_namespace: dict[str, list[str]] = {}
        for entry in all_entries:
            if candidate_id_counts[entry.id] == 1:
                ids_by_namespace.setdefault(entry.namespace, []).append(entry.id)
        stored_embeddings: dict[str, list[float]] = {}
        record_reader = getattr(backend, "get_vector_records", None)
        if not callable(record_reader):
            return _keyword_fallback()
        entries_by_key = {(entry.namespace, entry.id): entry for entry in all_entries}
        for candidate_namespace, candidate_ids in ids_by_namespace.items():
            records = record_reader(candidate_ids, namespace=candidate_namespace)
            for entry_id in candidate_ids:
                record = records.get(entry_id)
                entry = entries_by_key[(candidate_namespace, entry_id)]
                if (
                    record is not None
                    and record.provenance is not None
                    and record.provenance.matches(query_space, f"{entry.content} {entry.detail}", record.embedding)
                ):
                    stored_embeddings[entry_id] = list(record.embedding)
        if not stored_embeddings:
            logger.debug("semantic_recall_unqualified", reason="no_compatible_generation_records")
            return _keyword_fallback()

        signals = current_recall_signals()
        dense_batches: list[tuple[tuple[str, float], ...]] = []
        ranked = hybrid_search(
            query=query,
            entries=all_entries,
            scope=scope,
            embedder=embedder,
            query_embedding=query_vec,
            stored_embeddings=stored_embeddings or None,
            bm25_candidates=effective_bm25,
            vector_candidates=effective_vector,
            rrf_k=cfg.hybrid_rrf_k,
            # CORE116 RA2: utility belongs only in final relevance ties, never
            # in acquisition where it can discard the more relevant candidate.
            importance_alpha=1.0,
            top_k=top_k if not tags else max(top_k, namespace_size),
            # PRD-CORE-194 FR03: thread the bi-temporal validity prior into the
            # SAME hybrid pass so superseded records are excluded (or, with
            # ``as_of`` / ``include_superseded``, time-travelled) BEFORE the top_k
            # cut -- otherwise the prior could never re-include a record the hybrid
            # pass had already dropped.
            as_of=as_of,
            include_superseded=include_superseded,
            validity_reference_time=selection.reference_time,
            **(_DenseSearchOptions(dense_observer=dense_batches.append) if signals is not None else {}),
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
        selected = _union_id_lookups(ranked[:top_k])
        if signals is not None:
            dense_scores = dict(dense_batches[0]) if dense_batches else {}
            # Dense API keys are IDs, so do not ascribe an ambiguous ID or a
            # separately fetched ID-lookup object to a scored pool candidate.
            pool_objects = {id(entry) for entry in all_entries}
            id_counts = Counter(entry.id for entry in all_entries)
            unique_ids = {entry_id for entry_id, count in id_counts.items() if count == 1}
            for entry in selected:
                if (
                    id(entry) in pool_objects
                    and entry.id in unique_ids
                    and entry.id in dense_scores
                    and selection.eligible(entry)
                ):
                    signals.bind_dense(
                        entry,
                        query=query,
                        provider=embedder,
                        verified_space=query_space,
                        query_vector=query_vec,
                        store=backend,
                        namespace=entry.namespace,
                        entry_id=entry.id,
                        cosine=dense_scores[entry.id],
                    )
        return selected

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
