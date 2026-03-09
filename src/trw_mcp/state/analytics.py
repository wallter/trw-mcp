"""Learning analytics — re-export facade.

This module re-exports all public names from the four analytics sub-modules
so that existing ``from trw_mcp.state.analytics import X`` imports continue
to work without modification.

Sub-modules:
- analytics_core: singletons, constants, shared helpers
- analytics_entries: entry persistence, index management, status, extraction
- analytics_counters: analytics.yaml counter updates, event pattern detection
- analytics_dedup: deduplication, pruning, reflection quality scoring
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Module A — analytics_core (singletons, constants, shared helpers)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics_core import (
    __reload_hook__ as __reload_hook__,
    _entries_path as _entries_path,
    _ERROR_KEYWORDS as _ERROR_KEYWORDS,
    _get_event_type as _get_event_type,
    _iter_entry_files as _iter_entry_files,
    _safe_float as _safe_float,
    _safe_int as _safe_int,
    _SLUG_MAX_LEN as _SLUG_MAX_LEN,
    _SUCCESS_KEYWORDS as _SUCCESS_KEYWORDS,
    _TOPIC_KEYWORD_MAP as _TOPIC_KEYWORD_MAP,
    _TOPIC_TAG_MAX as _TOPIC_TAG_MAX,
    find_entry_by_id as find_entry_by_id,
    generate_learning_id as generate_learning_id,
    infer_topic_tags as infer_topic_tags,
    is_error_event as is_error_event,
    is_success_event as is_success_event,
)

# Re-export singletons so patch.object(analytics, "_config", ...) works.
# These are module-attribute references — updated by __reload_hook__()
# in analytics_core, which is re-exported above.
import trw_mcp.state.analytics_core as _ac  # noqa: E402

_config = _ac._config
_reader = _ac._reader
_writer = _ac._writer

# ---------------------------------------------------------------------------
# Module B — analytics_entries (persistence, index, status, extraction)
# ---------------------------------------------------------------------------
from trw_mcp.tools._learning_helpers import is_noise_summary as is_noise_summary

from trw_mcp.state.analytics_entries import (
    _save_and_record as _save_and_record,
    apply_status_update as apply_status_update,
    backfill_source_attribution as backfill_source_attribution,
    extract_learnings_from_llm as extract_learnings_from_llm,
    extract_learnings_mechanical as extract_learnings_mechanical,
    has_existing_mechanical_learning as has_existing_mechanical_learning,
    has_existing_success_learning as has_existing_success_learning,
    mark_promoted as mark_promoted,
    resync_learning_index as resync_learning_index,
    save_learning_entry as save_learning_entry,
    surface_validated_learnings as surface_validated_learnings,
    update_learning_index as update_learning_index,
)

# ---------------------------------------------------------------------------
# Module C — analytics_counters (counter updates, event patterns)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics_counters import (
    _read_analytics as _read_analytics,
    _update_core_counters as _update_core_counters,
    detect_tool_sequences as detect_tool_sequences,
    find_repeated_operations as find_repeated_operations,
    find_success_patterns as find_success_patterns,
    update_analytics as update_analytics,
    update_analytics_extended as update_analytics_extended,
    update_analytics_sync as update_analytics_sync,
)

# ---------------------------------------------------------------------------
# Module D — analytics_dedup (dedup, pruning, reflection quality)
# ---------------------------------------------------------------------------
from trw_mcp.state.analytics_dedup import (
    _compute_removal_scores as _compute_removal_scores,
    _compute_removal_scores_from_sqlite as _compute_removal_scores_from_sqlite,
    _score_impact_distribution as _score_impact_distribution,
    _score_learning_depth as _score_learning_depth,
    _score_learning_diversity as _score_learning_diversity,
    _select_removal_candidates as _select_removal_candidates,
    auto_prune_excess_entries as auto_prune_excess_entries,
    compute_jaccard_similarity as compute_jaccard_similarity,
    compute_reflection_quality as compute_reflection_quality,
    find_duplicate_learnings as find_duplicate_learnings,
)

# ---------------------------------------------------------------------------
# __all__ — complete public API
# ---------------------------------------------------------------------------
__all__ = [
    # analytics_core
    "__reload_hook__",
    "infer_topic_tags",
    "_entries_path",
    "_iter_entry_files",
    "_get_event_type",
    "is_error_event",
    "is_success_event",
    "find_entry_by_id",
    "generate_learning_id",
    "_safe_float",
    "_safe_int",
    # analytics_entries
    "surface_validated_learnings",
    "has_existing_success_learning",
    "has_existing_mechanical_learning",
    "save_learning_entry",
    "update_learning_index",
    "resync_learning_index",
    "mark_promoted",
    "apply_status_update",
    "_save_and_record",
    "extract_learnings_mechanical",
    "extract_learnings_from_llm",
    "backfill_source_attribution",
    # analytics_counters
    "find_repeated_operations",
    "find_success_patterns",
    "detect_tool_sequences",
    "_read_analytics",
    "_update_core_counters",
    "update_analytics",
    "update_analytics_sync",
    "update_analytics_extended",
    # analytics_dedup
    "compute_jaccard_similarity",
    "find_duplicate_learnings",
    "_compute_removal_scores",
    "_compute_removal_scores_from_sqlite",
    "_select_removal_candidates",
    "auto_prune_excess_entries",
    "_score_learning_diversity",
    "_score_learning_depth",
    "_score_impact_distribution",
    "compute_reflection_quality",
    # singletons (from analytics_core via module reference)
    "_config",
    "_reader",
    "_writer",
]
