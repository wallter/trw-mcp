"""Memory sub-package — clean import surface for memory-domain modules.

PRD-CORE-081 FR01: Provides a single import point for memory operations.
All memory-domain modules remain at their current paths for backward
compatibility; this package aggregates their public API.

Usage::

    from trw_mcp.state.memory import dedup_verdict, store_learning
"""

from __future__ import annotations

# --- Deduplication ---
from trw_mcp.state.dedup import (
    DedupResult as DedupResult,
)
from trw_mcp.state.dedup import (
    dedup_verdict as dedup_verdict,
)
from trw_mcp.state.dedup import (
    merge_into_survivor as merge_into_survivor,
)

# --- CRUD operations ---
from trw_mcp.state.memory_adapter import (
    count_entries as count_entries,
)

# --- Lookups ---
from trw_mcp.state.memory_adapter import (
    find_entry_by_id as find_entry_by_id,
)
from trw_mcp.state.memory_adapter import (
    find_yaml_path_for_entry as find_yaml_path_for_entry,
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
    record_surfaced as record_surfaced,
)
from trw_mcp.state.memory_adapter import (
    store_learning as store_learning,
)
from trw_mcp.state.memory_adapter import (
    update_learning as update_learning,
)

# --- Recall tracking & analytics ---
from trw_mcp.state.recall_tracking import (
    record_recall as record_recall,
)

# --- Tier management ---
from trw_mcp.state.tiers import (
    TierManager as TierManager,
)
from trw_mcp.state.tiers import (
    compute_importance_score as compute_importance_score,
)

__all__ = [
    "DedupResult",
    "TierManager",
    "compute_importance_score",
    "count_entries",
    "dedup_verdict",
    "find_entry_by_id",
    "find_yaml_path_for_entry",
    "list_active_learnings",
    "list_entries_by_status",
    "merge_into_survivor",
    "recall_learnings",
    "record_recall",
    "record_surfaced",
    "store_learning",
    "update_learning",
]
