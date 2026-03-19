"""Cluster detection for memory consolidation — FR01.

Entry loading (SQLite primary + YAML fallback), tag-overlap union-find
clustering, and embedding-based complete-linkage clustering.

NOTE: find_clusters looks up ``_tag_overlap_clusters`` from the parent
package at call time so that
``patch("trw_mcp.state.consolidation._tag_overlap_clusters")``
works after the flat module was converted to a package.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from trw_memory.lifecycle.consolidation import complete_linkage_cluster

from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state._helpers import iter_yaml_entry_files
from trw_mcp.state.dedup import cosine_similarity

if TYPE_CHECKING:
    from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Entry eligibility & loading
# ---------------------------------------------------------------------------


def _is_clusterable(data: LearningEntryDict) -> bool:
    """Check if an entry is eligible for clustering (not consolidated/archived)."""
    if str(data.get("source_type", "")) == "consolidated":
        return False
    return data.get("consolidated_into") is None


def _load_active_entries(
    entries_dir: Path,
    reader: FileStateReader,
    max_entries: int,
) -> list[LearningEntryDict]:
    """Load active learning entries for clustering, preferring SQLite.

    PRD-FIX-033-FR04: Attempts SQLite first, falls back to YAML glob.
    Filters out consolidated and archived entries in both paths.

    Args:
        entries_dir: Path to the learnings/entries/ directory.
        reader: FileStateReader for loading YAML entry files.
        max_entries: Cap on number of entries loaded.

    Returns:
        List of active entry dicts.
    """
    from trw_mcp.exceptions import StateError as _StateError

    entries: list[LearningEntryDict] = []

    # SQLite path
    try:
        from trw_mcp.state.memory_adapter import list_active_learnings

        trw_dir = entries_dir.parent.parent
        all_active = list_active_learnings(trw_dir, limit=max_entries)
        for data in all_active:
            if len(entries) >= max_entries:
                break
            if _is_clusterable(cast("LearningEntryDict", data)):
                entries.append(cast("LearningEntryDict", data))
        return entries
    except Exception as exc:  # justified: fail-open, consolidation errors must not block
        logger.warning(
            "sqlite_read_fallback",
            step="find_clusters",
            reason=str(exc),
        )

    # YAML fallback path
    for yaml_file in iter_yaml_entry_files(entries_dir):
        if yaml_file.name == "index.yaml":
            continue
        if len(entries) >= max_entries:
            break
        try:
            raw: dict[str, object] = reader.read_yaml(yaml_file)
        except (OSError, _StateError):
            continue
        if str(raw.get("status", "active")) != "active":
            continue
        yaml_entry = cast("LearningEntryDict", raw)
        if _is_clusterable(yaml_entry):
            entries.append(yaml_entry)

    return entries


# ---------------------------------------------------------------------------
# Tag-overlap clustering (union-find)
# ---------------------------------------------------------------------------


def _tag_overlap_clusters(
    entries: list[LearningEntryDict],
    *,
    min_cluster_size: int = 3,
    min_shared_tags: int = 2,
) -> list[list[LearningEntryDict]]:
    """Cluster entries by tag overlap using union-find.

    PRD-FIX-052-FR03: Fallback clustering when embeddings are unavailable.
    Two entries are considered similar if they share >= *min_shared_tags* tags.
    Clusters smaller than *min_cluster_size* are discarded.

    Args:
        entries: List of entry dicts with "tags" field.
        min_cluster_size: Minimum cluster size to keep (default 3).
        min_shared_tags: Minimum number of shared tags to merge entries (default 2).

    Returns:
        List of clusters; each cluster is a list of entry dicts.
    """
    n = len(entries)
    if n < min_cluster_size:
        return []

    # Pre-compute tag sets for each entry
    tag_sets: list[set[str]] = [
        {str(t) for t in (e.get("tags") or [] if isinstance(e.get("tags"), list) else [])} for e in entries
    ]

    # Union-Find parent array with path compression
    parent = list(range(n))

    def find(i: int) -> int:
        if parent[i] != i:
            parent[i] = find(parent[i])  # recursive path compression
        return parent[i]

    # Union entries with sufficient tag overlap
    for i in range(n):
        for j in range(i + 1, n):
            if len(tag_sets[i] & tag_sets[j]) >= min_shared_tags:
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pi] = pj

    # Collect groups by root
    groups: dict[int, list[LearningEntryDict]] = {}
    for i, entry in enumerate(entries):
        root = find(i)
        groups.setdefault(root, []).append(entry)

    # Return clusters above minimum size
    return [cluster for cluster in groups.values() if len(cluster) >= min_cluster_size]


# ---------------------------------------------------------------------------
# Embedding-based cluster detection (FR01)
# ---------------------------------------------------------------------------


def find_clusters(
    entries_dir: Path,
    reader: FileStateReader,
    *,
    similarity_threshold: float = 0.75,
    min_cluster_size: int = 3,
    max_entries: int = 50,
) -> list[list[LearningEntryDict]]:
    """Detect clusters of semantically similar active learning entries.

    Loads up to *max_entries* active entries, generates embeddings in a
    single batch call, then applies complete-linkage agglomerative
    clustering: two entries belong to the same cluster when every pair
    in the group has cosine similarity >= *similarity_threshold*.

    When embeddings are unavailable, falls back to tag-overlap clustering
    (PRD-FIX-052-FR03): entries sharing >= 2 tags are considered similar.
    The max_entries cap is NOT applied to the tag-based path (cap was for
    embedding API cost, irrelevant for local tag comparison).

    Args:
        entries_dir: Path to the learnings/entries/ directory.
        reader: FileStateReader for loading YAML entry files.
        similarity_threshold: Minimum pairwise similarity to merge into cluster.
        min_cluster_size: Clusters smaller than this are discarded.
        max_entries: Cap on number of entries loaded for the embedding path.

    Returns:
        List of clusters; each cluster is a list of entry dicts.
    """
    from trw_mcp.state.memory_adapter import embed_text_batch as embed_batch
    from trw_mcp.state.memory_adapter import embedding_available

    _t0 = time.monotonic()

    if not embedding_available():
        logger.debug("consolidation_embed_unavailable_using_tag_fallback")
        if not entries_dir.exists():
            return []
        # Tag-fallback: load ALL active entries (no max_entries cap)
        all_entries = _load_active_entries(entries_dir, reader, max_entries=10_000)
        # Late-bind from package so patch("trw_mcp.state.consolidation._tag_overlap_clusters") works
        _tag_cluster_fn = sys.modules["trw_mcp.state.consolidation"]._tag_overlap_clusters
        try:
            clusters: list[list[LearningEntryDict]] = _tag_cluster_fn(
                all_entries,
                min_cluster_size=min_cluster_size,
                min_shared_tags=2,
            )
        except Exception:
            logger.warning("tag_overlap_clustering_failed", exc_info=True)
            return []
        logger.debug(
            "find_clusters_tag_fallback_complete",
            duration_ms=round((time.monotonic() - _t0) * 1000, 2),
            cluster_count=len(clusters),
            entry_count=len(all_entries),
        )
        return clusters

    if not entries_dir.exists():
        return []

    entries = _load_active_entries(entries_dir, reader, max_entries)
    if len(entries) < min_cluster_size:
        return []

    # Batch embed all entries in one call (FR01 requirement)
    texts = [str(e.get("summary", "")) + " " + str(e.get("detail", "")) for e in entries]
    vectors = embed_batch(texts)

    # Build (entry, vector) pairs, dropping entries with no embedding
    indexed: list[tuple[LearningEntryDict, list[float]]] = []
    for i, vec in enumerate(vectors):
        if vec is not None:
            indexed.append((entries[i], vec))

    if len(indexed) < min_cluster_size:
        return []

    result = complete_linkage_cluster(
        indexed,
        similarity_threshold,
        min_cluster_size,
        similarity_fn=cosine_similarity,
    )

    logger.debug(
        "find_clusters_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        cluster_count=len(result),
        entry_count=len(entries),
    )

    return result
