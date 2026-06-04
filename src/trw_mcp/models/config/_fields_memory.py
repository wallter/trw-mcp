"""Memory, learning storage, hybrid retrieval, dedup, consolidation, and tiered fields.

Covers sections 3-7 of the original _main_fields.py:
  - Learning storage & retrieval
  - Hybrid retrieval (CORE-041)
  - Semantic dedup (CORE-042)
  - Memory consolidation (CORE-044)
  - Tiered memory (CORE-043)
"""

from __future__ import annotations

from pydantic import Field

from trw_mcp.models.config._defaults import (
    DEFAULT_LEARNING_MAX_ENTRIES,
    DEFAULT_RECALL_MAX_RESULTS,
    DEFAULT_RECALL_RECEIPT_MAX_ENTRIES,
)


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

    # -- Hybrid retrieval (CORE-041) --

    # PRD-INFRA-102 FR-03 clarification (2026-05-04):
    # `memory_store_path` is the SECONDARY embedding-sidecar path used by
    # `dedup.py` re-indexing via `MemoryStore` (creates `vec_entries` tables).
    # It is NOT the primary memory store path — that is hardcoded to
    # `<trw_dir>/memory/memory.db` in `_memory_connection.get_backend` and
    # contains the canonical `vec_memories` table. The field name's "memory
    # store" wording is historical (CORE-041 era) and misleading; do not
    # rename without coordinating with `_paths.resolve_memory_store_path`,
    # `dedup.py:367`, and tests in `test_retrieval.py`.
    memory_store_path: str = ".trw/memory/vectors.db"
    embeddings_enabled: bool = False
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
