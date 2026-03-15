"""Memory sub-package — clean import surface for memory-domain modules.

PRD-CORE-081 FR01: Provides a single import point for memory operations.
All memory-domain modules remain at their current paths for backward
compatibility; this package aggregates their public API.

Usage::

    from trw_mcp.state.memory import embed_text, check_duplicate, MemoryStore
"""

from __future__ import annotations

# --- consolidation: cluster-based learning consolidation ---
from trw_mcp.state.consolidation import (
    consolidate_cycle as consolidate_cycle,
)
from trw_mcp.state.consolidation import (
    find_clusters as find_clusters,
)

# --- dedup: semantic deduplication ---
from trw_mcp.state.dedup import (
    DedupResult as DedupResult,
)
from trw_mcp.state.dedup import (
    batch_dedup as batch_dedup,
)
from trw_mcp.state.dedup import (
    check_duplicate as check_duplicate,
)
from trw_mcp.state.dedup import (
    is_migration_needed as is_migration_needed,
)
from trw_mcp.state.dedup import (
    merge_entries as merge_entries,
)

# --- memory_adapter: embedding & backend access ---
from trw_mcp.state.memory_adapter import (
    backfill_embeddings as backfill_embeddings,
)
from trw_mcp.state.memory_adapter import (
    check_embeddings_status as check_embeddings_status,
)
from trw_mcp.state.memory_adapter import (
    count_entries as count_entries,
)
from trw_mcp.state.memory_adapter import (
    embed_text as embed_text,
)
from trw_mcp.state.memory_adapter import (
    embed_text_batch as embed_text_batch,
)
from trw_mcp.state.memory_adapter import (
    embedding_available as embedding_available,
)
from trw_mcp.state.memory_adapter import (
    ensure_migrated as ensure_migrated,
)
from trw_mcp.state.memory_adapter import (
    find_entry_by_id as find_entry_by_id,
)
from trw_mcp.state.memory_adapter import (
    find_yaml_path_for_entry as find_yaml_path_for_entry,
)
from trw_mcp.state.memory_adapter import (
    get_backend as get_backend,
)
from trw_mcp.state.memory_adapter import (
    get_embedder as get_embedder,
)
from trw_mcp.state.memory_adapter import (
    list_active_learnings as list_active_learnings,
)
from trw_mcp.state.memory_adapter import (
    list_entries_by_status as list_entries_by_status,
)
from trw_mcp.state.memory_adapter import (
    recall_learnings as recall_learnings,
)
from trw_mcp.state.memory_adapter import (
    reset_backend as reset_backend,
)
from trw_mcp.state.memory_adapter import (
    reset_embedder as reset_embedder,
)
from trw_mcp.state.memory_adapter import (
    store_learning as store_learning,
)
from trw_mcp.state.memory_adapter import (
    update_access_tracking as update_access_tracking,
)
from trw_mcp.state.memory_adapter import (
    update_learning as update_learning,
)

# --- memory_store: sqlite-vec vector store ---
from trw_mcp.state.memory_store import (
    MemoryStore as MemoryStore,
)

# --- recall_tracking: recall analytics ---
from trw_mcp.state.recall_tracking import (
    get_recall_stats as get_recall_stats,
)
from trw_mcp.state.recall_tracking import (
    record_outcome as record_outcome,
)
from trw_mcp.state.recall_tracking import (
    record_recall as record_recall,
)

# --- tiers: importance scoring and tier management ---
from trw_mcp.state.tiers import (
    TierManager as TierManager,
)
from trw_mcp.state.tiers import (
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
