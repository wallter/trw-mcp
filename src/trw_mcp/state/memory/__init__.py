"""Memory sub-package — clean import surface for memory-domain modules.

PRD-CORE-081 FR01: Provides a single import point for memory operations.
All memory-domain modules remain at their current paths for backward
compatibility; this package aggregates their public API.

Usage::

    from trw_mcp.state.memory import embed_text, check_duplicate, MemoryStore
"""

from __future__ import annotations

# --- Deduplication & consolidation ---
from trw_mcp.state.consolidation import (
    consolidate_cycle as consolidate_cycle,
    find_clusters as find_clusters,
)
from trw_mcp.state.dedup import (
    DedupResult as DedupResult,
    batch_dedup as batch_dedup,
    check_duplicate as check_duplicate,
    is_migration_needed as is_migration_needed,
    merge_entries as merge_entries,
)

# --- Connection & backend management ---
from trw_mcp.state.memory_adapter import (
    ensure_migrated as ensure_migrated,
    get_backend as get_backend,
    reset_backend as reset_backend,
)

# --- Embedding operations ---
from trw_mcp.state.memory_adapter import (
    backfill_embeddings as backfill_embeddings,
    check_embeddings_status as check_embeddings_status,
    embed_text as embed_text,
    embed_text_batch as embed_text_batch,
    embedding_available as embedding_available,
    get_embedder as get_embedder,
    reset_embedder as reset_embedder,
)

# --- CRUD operations ---
from trw_mcp.state.memory_adapter import (
    count_entries as count_entries,
    find_entry_by_id as find_entry_by_id,
    find_yaml_path_for_entry as find_yaml_path_for_entry,
    list_active_learnings as list_active_learnings,
    list_entries_by_status as list_entries_by_status,
    recall_learnings as recall_learnings,
    store_learning as store_learning,
    update_access_tracking as update_access_tracking,
    update_learning as update_learning,
)

# --- Vector store ---
from trw_mcp.state.memory_store import (
    MemoryStore as MemoryStore,
)

# --- Recall tracking & analytics ---
from trw_mcp.state.recall_tracking import (
    get_recall_stats as get_recall_stats,
    record_outcome as record_outcome,
    record_recall as record_recall,
)

# --- Tier management ---
from trw_mcp.state.tiers import (
    TierManager as TierManager,
    compute_importance_score as compute_importance_score,
)

__all__ = [
    "DedupResult",
    "MemoryStore",
    "TierManager",
    "backfill_embeddings",
    "batch_dedup",
    "check_duplicate",
    "check_embeddings_status",
    "compute_importance_score",
    "consolidate_cycle",
    "count_entries",
    "embed_text",
    "embed_text_batch",
    "embedding_available",
    "ensure_migrated",
    "find_clusters",
    "find_entry_by_id",
    "find_yaml_path_for_entry",
    "get_backend",
    "get_embedder",
    "get_recall_stats",
    "is_migration_needed",
    "list_active_learnings",
    "list_entries_by_status",
    "merge_entries",
    "recall_learnings",
    "record_outcome",
    "record_recall",
    "reset_backend",
    "reset_embedder",
    "store_learning",
    "update_access_tracking",
    "update_learning",
]
