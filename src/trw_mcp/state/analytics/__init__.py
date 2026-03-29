"""Learning analytics — re-export facade.

This package re-exports all public names from the four analytics sub-modules
so that existing ``from trw_mcp.state.analytics import X`` imports continue
to work without modification.

Sub-modules:
- analytics.core: singletons, constants, shared helpers
- analytics.entries: entry persistence, index management, status, extraction
- analytics.counters: analytics.yaml counter updates, event pattern detection
- analytics.dedup: deduplication, pruning, reflection quality scoring
"""

from __future__ import annotations

from trw_mcp.state.analytics.core import (
    _ERROR_KEYWORDS as _ERROR_KEYWORDS,
)
from trw_mcp.state.analytics.core import (
    _SLUG_MAX_LEN as _SLUG_MAX_LEN,
)
from trw_mcp.state.analytics.core import (
    _SUCCESS_KEYWORDS as _SUCCESS_KEYWORDS,
)
from trw_mcp.state.analytics.core import (
    _TOPIC_KEYWORD_MAP as _TOPIC_KEYWORD_MAP,
)
from trw_mcp.state.analytics.core import (
    _TOPIC_TAG_MAX as _TOPIC_TAG_MAX,
)

# ---------------------------------------------------------------------------
# Module A — core (singletons, constants, shared helpers)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics.core import (
    _entries_path as _entries_path,
)
from trw_mcp.state.analytics.core import (
    _get_event_type as _get_event_type,
)
from trw_mcp.state.analytics.core import (
    _iter_entry_files as _iter_entry_files,
)
from trw_mcp.state.analytics.core import (
    _safe_float as _safe_float,
)
from trw_mcp.state.analytics.core import (
    _safe_int as _safe_int,
)
from trw_mcp.state.analytics.core import (
    find_entry_by_id as find_entry_by_id,
)
from trw_mcp.state.analytics.core import (
    generate_learning_id as generate_learning_id,
)
from trw_mcp.state.analytics.core import (
    infer_topic_tags as infer_topic_tags,
)
from trw_mcp.state.analytics.core import (
    is_error_event as is_error_event,
)
from trw_mcp.state.analytics.core import (
    is_success_event as is_success_event,
)

# ---------------------------------------------------------------------------
# Module C — counters (counter updates, event patterns)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics.counters import (
    _read_analytics as _read_analytics,
)
from trw_mcp.state.analytics.counters import (
    _update_core_counters as _update_core_counters,
)
from trw_mcp.state.analytics.counters import (
    detect_tool_sequences as detect_tool_sequences,
)
from trw_mcp.state.analytics.counters import (
    find_repeated_operations as find_repeated_operations,
)
from trw_mcp.state.analytics.counters import (
    find_success_patterns as find_success_patterns,
)
from trw_mcp.state.analytics.counters import (
    update_analytics as update_analytics,
)
from trw_mcp.state.analytics.counters import (
    update_analytics_extended as update_analytics_extended,
)
from trw_mcp.state.analytics.counters import (
    update_analytics_sync as update_analytics_sync,
)

# ---------------------------------------------------------------------------
# Module D — dedup (dedup, pruning, reflection quality)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics.dedup import (
    _compute_removal_scores as _compute_removal_scores,
)
from trw_mcp.state.analytics.dedup import (
    _compute_removal_scores_from_sqlite as _compute_removal_scores_from_sqlite,
)
from trw_mcp.state.analytics.dedup import (
    _score_impact_distribution as _score_impact_distribution,
)
from trw_mcp.state.analytics.dedup import (
    _score_learning_depth as _score_learning_depth,
)
from trw_mcp.state.analytics.dedup import (
    _score_learning_diversity as _score_learning_diversity,
)
from trw_mcp.state.analytics.dedup import (
    _select_removal_candidates as _select_removal_candidates,
)
from trw_mcp.state.analytics.dedup import (
    auto_prune_excess_entries as auto_prune_excess_entries,
)
from trw_mcp.state.analytics.dedup import (
    compute_jaccard_similarity as compute_jaccard_similarity,
)
from trw_mcp.state.analytics.dedup import (
    compute_reflection_quality as compute_reflection_quality,
)
from trw_mcp.state.analytics.dedup import (
    find_duplicate_learnings as find_duplicate_learnings,
)
from trw_mcp.state.analytics.entries import (
    _save_and_record as _save_and_record,
)
from trw_mcp.state.analytics.entries import (
    apply_status_update as apply_status_update,
)
from trw_mcp.state.analytics.entries import (
    backfill_source_attribution as backfill_source_attribution,
)
from trw_mcp.state.analytics.entries import (
    extract_learnings_from_llm as extract_learnings_from_llm,
)
from trw_mcp.state.analytics.entries import (
    extract_learnings_mechanical as extract_learnings_mechanical,
)
from trw_mcp.state.analytics.entries import (
    has_existing_mechanical_learning as has_existing_mechanical_learning,
)
from trw_mcp.state.analytics.entries import (
    has_existing_success_learning as has_existing_success_learning,
)
from trw_mcp.state.analytics.entries import (
    mark_promoted as mark_promoted,
)
from trw_mcp.state.analytics.entries import (
    resync_learning_index as resync_learning_index,
)
from trw_mcp.state.analytics.entries import (
    save_learning_entry as save_learning_entry,
)
from trw_mcp.state.analytics.entries import (
    surface_validated_learnings as surface_validated_learnings,
)
from trw_mcp.state.analytics.entries import (
    update_learning_index as update_learning_index,
)

# ---------------------------------------------------------------------------
# Module A — core: noise detection (PRD-FIX-061-FR01)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics.core import is_noise_summary as is_noise_summary

# ---------------------------------------------------------------------------
# __all__ — public API (FR04: private names removed from __all__)
# ---------------------------------------------------------------------------
__all__ = [
    "apply_status_update",
    "auto_prune_excess_entries",
    "backfill_source_attribution",
    "compute_jaccard_similarity",
    "compute_reflection_quality",
    "detect_tool_sequences",
    "extract_learnings_from_llm",
    "extract_learnings_mechanical",
    "find_duplicate_learnings",
    "find_entry_by_id",
    "find_repeated_operations",
    "find_success_patterns",
    "generate_learning_id",
    "has_existing_mechanical_learning",
    "has_existing_success_learning",
    "infer_topic_tags",
    "is_error_event",
    "is_noise_summary",
    "is_success_event",
    "mark_promoted",
    "resync_learning_index",
    "save_learning_entry",
    "surface_validated_learnings",
    "update_analytics",
    "update_analytics_extended",
    "update_analytics_sync",
    "update_learning_index",
]
