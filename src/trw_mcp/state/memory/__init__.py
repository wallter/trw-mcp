"""Memory sub-package — clean import surface for memory-domain modules.

PRD-CORE-081 FR01: Provides a single import point for memory operations.
All memory-domain modules remain at their current paths for backward
compatibility; this package aggregates their public API.

Usage::

    from trw_mcp.state.memory import embed_text, check_duplicate, MemoryStore
"""

from __future__ import annotations

# --- memory_adapter: embedding & backend access ---
from trw_mcp.state.memory_adapter import (
    backfill_embeddings as backfill_embeddings,
    check_embeddings_status as check_embeddings_status,
    count_entries as count_entries,
    embed_text as embed_text,
    embed_text_batch as embed_text_batch,
    embedding_available as embedding_available,
    ensure_migrated as ensure_migrated,
    find_entry_by_id as find_entry_by_id,
    find_yaml_path_for_entry as find_yaml_path_for_entry,
    get_backend as get_backend,
    get_embedder as get_embedder,
    list_active_learnings as list_active_learnings,
    list_entries_by_status as list_entries_by_status,
    recall_learnings as recall_learnings,
    reset_backend as reset_backend,
    reset_embedder as reset_embedder,
    store_learning as store_learning,
    update_access_tracking as update_access_tracking,
    update_learning as update_learning,
)

# --- dedup: semantic deduplication ---
from trw_mcp.state.dedup import (
    DedupResult as DedupResult,
    batch_dedup as batch_dedup,
    check_duplicate as check_duplicate,
    is_migration_needed as is_migration_needed,
    merge_entries as merge_entries,
)

# --- consolidation: cluster-based learning consolidation ---
from trw_mcp.state.consolidation import (
    consolidate_cycle as consolidate_cycle,
    find_clusters as find_clusters,
)

# --- memory_store: sqlite-vec vector store ---
from trw_mcp.state.memory_store import (
    MemoryStore as MemoryStore,
)

# --- recall_tracking: recall analytics ---
from trw_mcp.state.recall_tracking import (
    get_recall_stats as get_recall_stats,
    record_outcome as record_outcome,
    record_recall as record_recall,
)

# --- tiers: importance scoring and tier management ---
from trw_mcp.state.tiers import (
    TierManager as TierManager,
    compute_importance_score as compute_importance_score,
)

__all__ = [
    # memory_adapter
    "backfill_embeddings",
    "check_embeddings_status",
    "count_entries",
    "embed_text",
    "embed_text_batch",
    "embedding_available",
    "ensure_migrated",
    "find_entry_by_id",
    "find_yaml_path_for_entry",
    "get_backend",
    "get_embedder",
    "list_active_learnings",
    "list_entries_by_status",
    "recall_learnings",
    "reset_backend",
    "reset_embedder",
    "store_learning",
    "update_access_tracking",
    "update_learning",
    # dedup
    "DedupResult",
    "batch_dedup",
    "check_duplicate",
    "is_migration_needed",
    "merge_entries",
    # consolidation
    "consolidate_cycle",
    "find_clusters",
    # memory_store
    "MemoryStore",
    # recall_tracking
    "get_recall_stats",
    "record_outcome",
    "record_recall",
    # tiers
    "TierManager",
    "compute_importance_score",
]
