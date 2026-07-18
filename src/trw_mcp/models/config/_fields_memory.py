"""Memory storage, retrieval, deduplication, consolidation, and tier fields.

Extracted from sections 3-7 of the original ``_main_fields.py``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from trw_mcp.models.config._defaults import (
    DEFAULT_LEARNING_MAX_ENTRIES,
    DEFAULT_RECALL_INTERNAL_FIELDS,
    DEFAULT_RECALL_MAX_RESULTS,
    DEFAULT_RECALL_RECEIPT_MAX_ENTRIES,
)

# PRD-CORE-202-NFR05: per-external-store recall cap default — mirrors the
# CORE-185 ``recall_user_tier_cap`` default (5) so the two tier caps move together.
DEFAULT_EXTERNAL_STORE_RECALL_CAP = 5


class _MemoryFields:
    """Memory domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Learning storage & retrieval --

    learning_max_entries: int = DEFAULT_LEARNING_MAX_ENTRIES
    learning_promotion_impact: float = 0.7
    learning_prune_age_days: int = 30
    learning_repeated_op_threshold: int = 3
    recall_receipt_max_entries: int = DEFAULT_RECALL_RECEIPT_MAX_ENTRIES
    recall_max_results: int = DEFAULT_RECALL_MAX_RESULTS
    recall_compact_fields: frozenset[str] = frozenset({"id", "summary", "impact", "tags", "status"})
    recall_internal_fields: frozenset[str] = DEFAULT_RECALL_INTERNAL_FIELDS

    # -- Hybrid retrieval (CORE-041) --

    # Secondary embedding sidecar used by dedup re-indexing; the canonical
    # store remains <trw_dir>/memory/memory.db. Coordinate any rename with
    # _paths.resolve_memory_store_path and dedup.py.
    memory_store_path: str = ".trw/memory/vectors.db"
    # Hybrid retrieval defaults on; initialization remains non-blocking and
    # degrades to keyword search until the embedder is ready. Operators may opt out.
    embeddings_enabled: bool = True
    retrieval_embedding_model: str = "all-MiniLM-L6-v2"
    retrieval_embedding_dim: int = 384
    # PRD-FIX-COMPOUNDING-3-FR02: Coverage warning threshold for coverage_probe.
    # When coverage_ratio < this value, check_embeddings_status() emits an advisory.
    # Default 0.10 (10%): fires on the current 3.6% post-recovery state; silent above 10%.
    embeddings_coverage_warn_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    # PRD-FIX-105-FR01: When session_start detects low vector coverage (advisory
    # set), schedule a BACKGROUND backfill thread so the corpus self-heals instead
    # of the advisory crying wolf forever with no remediation. Uses the singleton
    # _schedule_post_recovery_backfill thread guard (one backfill at a time, no-op
    # while running), so it never starves the shared HTTP hot path the way a
    # synchronous backfill would. Set False to keep the old advisory-only posture.
    embeddings_auto_backfill_on_low_coverage: bool = True
    hybrid_bm25_candidates: int = 50
    hybrid_vector_candidates: int = 50
    # PRD-DIST-254 §FR03 follow-up (2026-06-10): the in-process MCP recall path
    # (`_memory_queries._search_entries`) historically ranked only a ~75-record
    # candidate slice — the ≤25 LIKE-substring keyword hits plus
    # `hybrid_vector_candidates` vector hits — and fused them with a LIKE keyword
    # ranker whose order is near-noise on a natural-language query. On the 226-
    # record operator gold set this collapsed embeddings-ON Recall@5 to 0.583
    # (vs MemoryClient 0.9375) even though the gold record sat at vector rank 0
    # for 18/24 queries: pure-position RRF let ~10 junk LIKE hits leapfrog the
    # correct vector hit. The hybrid branch now loads up to this many entries and
    # ranks them with the SAME `trw_memory.retrieval.pipeline.hybrid_search`
    # (BM25 + dense + RRF) the MemoryClient path uses, so the two paths agree.
    # Default 1000 mirrors `MemoryConfig.hybrid_search_candidate_pool_size`.
    hybrid_search_candidate_pool_size: int = Field(default=1000, ge=1)
    hybrid_rrf_k: int = 60
    # R-FUSION-001 / F15: blend learning importance into the in-process recall's
    # positional RRF fusion (`_memory_queries._search_entries`), matching the
    # MemoryClient path (`trw_memory.retrieval.pipeline.hybrid_search` →
    # `rrf_fuse(..., alpha=...)`). The bare RRF score is position-only, so two
    # results at the same fused rank tie even when one is impact-0.95 tribal
    # knowledge and the other is impact-0.2 noise. `final = alpha * rrf_norm +
    # (1 - alpha) * importance`. 1.0 = pure position (legacy back-compat, no
    # importances passed), 0.0 = pure importance. Default 0.7 mirrors the
    # MemoryClient default (`MemoryConfig.rrf_importance_alpha`).
    hybrid_rrf_importance_alpha: float = Field(default=0.7, ge=0.0, le=1.0)
    hybrid_reranking_enabled: bool = False
    retrieval_fallback_enabled: bool = True
    wal_checkpoint_threshold_mb: int = 10

    # -- LLM utility filter (QUAL-062) --
    # When enabled, trw_learn routes each candidate learning through a live
    # Claude Haiku call (is_high_utility) that can reject low-utility entries.
    # Default False: the call has undisclosed latency + API cost and fails open,
    # so it must be opted into explicitly rather than firing on every learn.
    llm_utility_filter_enabled: bool = False

    # -- Semantic dedup (CORE-042) --

    dedup_enabled: bool = True
    dedup_skip_threshold: float = 0.95
    dedup_merge_threshold: float = 0.85

    # -- Memory consolidation (CORE-044, FIX-071) --

    memory_consolidation_enabled: bool = True
    memory_consolidation_interval_days: int = 7
    memory_consolidation_min_cluster: int = Field(default=3, ge=2)
    memory_consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    memory_consolidation_max_per_cycle: int = Field(default=50, ge=1)
    max_cluster_size: int = Field(default=10, ge=2)
    max_consolidated_tags: int = Field(default=20, ge=5)

    # -- Tiered memory (CORE-043) --

    memory_hot_max_entries: int = 50
    memory_hot_ttl_days: int = 7
    memory_cold_threshold_days: int = 90
    memory_retention_days: int = 365
    memory_score_w1: float = 0.4
    memory_score_w2: float = 0.3
    memory_score_w3: float = 0.3

    # -- External read-stores (PRD-CORE-202) --
    # One or more EXTERNAL trw-memory SQLite DBs registered as READ-ONLY sources
    # unioned into ``trw_recall`` (e.g. a consolidated/distributable corpus built
    # by trw-distill). Each entry is a filesystem path to a trw-memory ``memory.db``.
    # The default empty list preserves today's single-backend behavior exactly:
    # with no entries (and no ``--memory-db`` flag), the external-federation step
    # is skipped entirely (NFR01) and recall is byte-identical to HEAD (NFR06).
    # External corpora are NEVER a write destination (FR04 / NFR02). May also be
    # supplied via the ``TRW_EXTRA_READ_STORES`` env var (a JSON path-list) or the
    # ``--memory-db`` startup flag (FR03).
    extra_read_stores: list[Path] = Field(
        default_factory=list,
        description=(
            "External trw-memory SQLite DBs registered as READ-ONLY sources unioned "
            "into trw_recall (PRD-CORE-202 FR01). Empty (default) = project/user only. "
            "Also settable via TRW_EXTRA_READ_STORES (JSON list) or --memory-db."
        ),
    )
    # PRD-CORE-202-NFR05: bounds the records loaded per external store so a large
    # (~1,200-record) corpus cannot inflate per-query latency or bury project
    # precision. Typed Field(ge=1), not a literal (no magic numbers).
    external_store_recall_cap: int = Field(
        default=DEFAULT_EXTERNAL_STORE_RECALL_CAP,
        ge=1,
        description=(
            "Maximum number of hits each external read-store may contribute to a "
            "single federated recall result (PRD-CORE-202 NFR05)."
        ),
    )

    # -- Learning recall control (S7, PRD-CORE-125) --

    learning_recall_enabled: bool | None = None
    learning_injection_preview_chars: int = Field(default=500, ge=50, le=2000)
    session_start_recall_enabled: bool | None = None

    # -- Chain-mode recency bypass (L-fovv fix, 2026-04-21, iter-18 follow-up) --
    # trw_session_start wildcard recall filters at min_impact=0.7 which excludes
    # fresh low-impact learnings (trw_learn defaults to impact=0.5). In chain-mode
    # this means link 2+ cannot see link 1's learnings. The bypass does a union
    # recall: high-impact baseline (preserves current behavior) + fresh low-impact
    # (surfaces per-project session context). Set days=0 to disable the bypass.
    session_start_recent_bypass_days: int = Field(default=7, ge=0, le=365)
    session_start_recent_bypass_min_impact: float = Field(default=0.3, ge=0.0, le=1.0)

    # -- Session-start runtime pressure controls (PRD-FIX-080) --
    # SQLite uses a 30s busy timeout. In shared MCP workspaces, best-effort
    # session-start writes must not stack several lock waits before returning
    # learnings to the caller. These defaults preserve normal single-writer
    # behavior while deferring non-critical side effects when another live MCP
    # process is already registered against the same memory DB.
    session_start_defer_under_writer_pressure: bool = True
    session_start_writer_pressure_threshold: int = Field(default=2, ge=2, le=64)
