"""Memory storage, retrieval, deduplication, consolidation, and tier fields.

Extracted from sections 3-7 of the original ``_main_fields.py``.
"""

from __future__ import annotations

from pydantic import Field

from trw_mcp.models.config._defaults import (
    DEFAULT_LEARNING_MAX_ENTRIES,
    DEFAULT_RECALL_INTERNAL_FIELDS,
    DEFAULT_RECALL_MAX_RESULTS,
    DEFAULT_RECALL_RECEIPT_MAX_ENTRIES,
)


class _MemoryFields:
    """Memory domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Learning storage & retrieval --

    learning_max_entries: int = DEFAULT_LEARNING_MAX_ENTRIES
    learning_promotion_impact: float = 0.7
    # learning_prune_age_days and memory_consolidation_interval_days were removed 2026-09-16
    # (PRD-QUAL-139-FR05): no consumer under the corrected scan, no originating PRD, and only
    # default pins in tests. Both keys are listed in trw_mcp/data/config-retired-keys.json.
    learning_repeated_op_threshold: int = 3
    recall_receipt_max_entries: int = DEFAULT_RECALL_RECEIPT_MAX_ENTRIES
    recall_max_results: int = DEFAULT_RECALL_MAX_RESULTS
    recall_internal_fields: frozenset[str] = DEFAULT_RECALL_INTERNAL_FIELDS

    # -- Store selection (PRD-CORE-280 FR01) --
    # Pinned by ``memory migrate`` (PRD-CORE-298). Empty = unmigrated checkout on its own
    # memory.db; set = the daemon store serves this namespace and memory tools fail closed
    # until it is reachable.
    project_namespace: str = ""

    # -- Hybrid retrieval (CORE-041) --

    # Hybrid retrieval defaults on; initialization remains non-blocking and
    # degrades to keyword search until the embedder is ready. Operators may opt out.
    embeddings_enabled: bool = True
    # retrieval_embedding_model was removed in 7.0.0: the daemon's MEMORY_EMBEDDING_MODEL
    # chooses the encoder. The key is listed in trw_mcp/data/config-retired-keys.json.
    # PRD-FIX-COMPOUNDING-3-FR02: Coverage warning threshold for pipeline health.
    # When coverage_ratio < this value, probe_embedding_coverage() reports degraded.
    # Default 0.10 (10%): fires on the current 3.6% post-recovery state; silent above 10%.
    embeddings_coverage_warn_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    # PRD-CORE-292: the recall candidate pool and BM25/vector candidate caps are the
    # library's (``MemoryConfig``: MEMORY_HYBRID_SEARCH_CANDIDATE_POOL_SIZE,
    # MEMORY_BM25_CANDIDATES, MEMORY_VECTOR_CANDIDATES); the duplicate
    # hybrid_bm25_candidates / hybrid_vector_candidates /
    # hybrid_search_candidate_pool_size fields were removed. extra="ignore" plus the
    # unrecognised-key warning means an old config still loads and names the key.
    hybrid_rrf_k: int = 60
    # -- Semantic dedup (CORE-042) --

    dedup_enabled: bool = True
    dedup_skip_threshold: float = 0.95
    dedup_merge_threshold: float = 0.85

    # -- Memory consolidation (CORE-044, FIX-071) --

    memory_consolidation_enabled: bool = True
    memory_consolidation_min_cluster: int = Field(default=3, ge=2)
    memory_consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    memory_consolidation_max_per_cycle: int = Field(default=50, ge=1)
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

    learning_recall_enabled: bool = True
    session_start_recall_enabled: bool | None = None

    # -- Chain-mode recency bypass (L-fovv fix, 2026-04-21, iter-18 follow-up) --
    # trw_session_start wildcard recall filters at min_impact=0.7 which excludes
    # fresh low-impact learnings (trw_learn defaults to impact=0.5). In chain-mode
    # this means link 2+ cannot see link 1's learnings. The bypass does a union
    # recall: high-impact baseline (preserves current behavior) + fresh low-impact
    # (surfaces per-project session context). Set days=0 to disable the bypass.

    # -- Session-start runtime pressure controls removed (PRD-CORE-280 FR01) --
    # session_start_defer_under_writer_pressure, session_start_writer_pressure_threshold,
    # and session_start_max_deferral_hours were retired: TRW now runs every
    # session_start step unconditionally rather than deferring under writer
    # contention. See data/config-retired-keys.json.
