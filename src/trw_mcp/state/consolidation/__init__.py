"""Memory consolidation engine — PRD-CORE-044.

Clusters semantically similar learning entries using embeddings and
complete-linkage agglomerative clustering, then consolidates each cluster
into a single entry via LLM summarization (with a longest-entry fallback).
Original entries are archived to the cold tier after consolidation.

This package decomposes the consolidation engine into focused modules:

- ``_clustering`` — entry loading, tag-overlap union-find, embedding clusters
- ``_summarize`` — LLM summarization with retry + longest-entry fallback
- ``_archive``   — original entry archival with atomic rollback
- ``_cycle``     — main consolidation cycle, entry creation, dry-run mode

All public names are re-exported here for backward compatibility.
"""

from __future__ import annotations

# --- Re-export from trw_memory (used directly by tests via this module) ---
from trw_memory.lifecycle.consolidation import (
    _redact_paths as _redact_paths,
)

# --- Re-export LLMClient so patch targets like
#     "trw_mcp.state.consolidation.LLMClient" continue to resolve ---
from trw_mcp.clients.llm import LLMClient as LLMClient

# --- Re-export get_config for patch target
#     "trw_mcp.state.consolidation.get_config" ---
from trw_mcp.models.config import get_config as get_config

# --- Re-export _parse_consolidation_response from local _summarize ---
# (moved from trw_memory after dead code removal there)
from trw_mcp.state.consolidation._summarize import (
    _parse_consolidation_response as _parse_consolidation_response,
)

# --- Archival: archive + rollback ---
from ._archive import (
    _archive_originals as _archive_originals,
)
from ._archive import (
    _rollback_archive as _rollback_archive,
)

# --- Clustering: entry loading, tag overlap, embedding clusters ---
from ._clustering import (
    _is_clusterable as _is_clusterable,
)
from ._clustering import (
    _load_active_entries as _load_active_entries,
)
from ._clustering import (
    _tag_overlap_clusters as _tag_overlap_clusters,
)
from ._clustering import (
    find_clusters as find_clusters,
)

# --- Cycle: entry creation, dry-run, main entry point ---
from ._audit_patterns import (
    detect_audit_finding_recurrence as detect_audit_finding_recurrence,
)
from ._cycle import (
    _create_consolidated_entry as _create_consolidated_entry,
)
from ._cycle import (
    _mean_pairwise_similarity as _mean_pairwise_similarity,
)
from ._cycle import (
    consolidate_cycle as consolidate_cycle,
)
# --- Summarization: LLM + fallback ---
from ._summarize import (
    _summarize_cluster_fallback as _summarize_cluster_fallback,
)
from ._summarize import (
    _summarize_cluster_llm as _summarize_cluster_llm,
)

__all__ = [
    "LLMClient",
    "_archive_originals",
    "_create_consolidated_entry",
    "_is_clusterable",
    "_load_active_entries",
    "_mean_pairwise_similarity",
    "_parse_consolidation_response",
    "_redact_paths",
    "_rollback_archive",
    "_summarize_cluster_fallback",
    "_tag_overlap_clusters",
    "consolidate_cycle",
    "detect_audit_finding_recurrence",
    "find_clusters",
    "get_config",
]
