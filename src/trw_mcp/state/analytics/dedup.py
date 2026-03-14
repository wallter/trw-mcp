"""Analytics dedup — deduplication, pruning, and reflection quality scoring.

Module D of the analytics decomposition.  Handles Jaccard similarity,
duplicate detection, utility-based pruning, auto-prune orchestration,
and composite reflection quality scoring.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

import trw_mcp.state.analytics.core as _ac
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state._helpers import is_active_entry
from trw_mcp.state.analytics.entries import apply_status_update, resync_learning_index
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Dedup and pruning
# ---------------------------------------------------------------------------


def compute_jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings using word tokens.

    PRD-QUAL-012-FR06: Used for dedup detection between learning summaries.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Jaccard index in [0.0, 1.0]. 1.0 means identical token sets.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def find_duplicate_learnings(
    entries_dir: Path,
    threshold: float = 0.8,
    *,
    entries: list[LearningEntryDict] | None = None,
) -> list[tuple[str, str, float]]:
    """Find duplicate learning entries by Jaccard similarity on summaries.

    PRD-QUAL-012-FR06: Identifies pairs of active learnings whose summaries
    overlap above the threshold. The older entry in each pair is the
    candidate for dedup (pruning).

    PRD-FIX-033-FR03: When *entries* is provided (pre-loaded from SQLite),
    skip ``_iter_entry_files()`` and compute Jaccard directly on the list.

    Args:
        entries_dir: Path to entries directory.
        threshold: Minimum Jaccard similarity to flag as duplicate.
        entries: Optional pre-loaded list of entry dicts. When provided,
            the YAML scan is skipped entirely.

    Returns:
        List of (older_id, newer_id, similarity) tuples.
    """
    if entries is not None:
        # PRD-FIX-033-FR03: Use pre-loaded entries (from SQLite)
        active_entries: list[LearningEntryDict] = [
            e for e in entries
            if str(e.get("status", "active")) == "active"
        ]
    else:
        # Backward-compatible YAML scan path
        if not entries_dir.is_dir():
            return []
        active_entries = []
        for _path, data in _ac._iter_entry_files(entries_dir, sorted_order=True):
            if is_active_entry(data):
                active_entries.append(cast("LearningEntryDict", data))

    duplicates: list[tuple[str, str, float]] = []
    for i, entry_a in enumerate(active_entries):
        summary_a = str(entry_a.get("summary", ""))
        for entry_b in active_entries[i + 1:]:
            summary_b = str(entry_b.get("summary", ""))
            sim = compute_jaccard_similarity(summary_a, summary_b)
            if sim >= threshold:
                id_a = str(entry_a.get("id", ""))
                id_b = str(entry_b.get("id", ""))
                duplicates.append((id_a, id_b, round(sim, 3)))
    return duplicates


def _compute_removal_scores(
    entries_tuples: list[tuple[Path, dict[str, object]]],
    entries_dir: Path,
    jaccard_threshold: float,
) -> tuple[list[tuple[str, str, float]], list[dict[str, object]]]:
    """Compute removal scores for a set of entries.

    Step 1 identifies Jaccard duplicates; step 2 computes utility-based
    prune candidates for the full entry set.

    Args:
        entries_tuples: List of (file_path, entry_data) tuples.
        entries_dir: Path to the entries directory (used by Jaccard scan).
        jaccard_threshold: Minimum Jaccard similarity to flag as duplicate.

    Returns:
        Tuple of (duplicates, utility_candidates) where:
        - duplicates: list of (older_id, newer_id, similarity) from Jaccard scan
        - utility_candidates: list of candidate dicts from utility_based_prune_candidates
    """
    from trw_mcp.scoring import utility_based_prune_candidates

    duplicates = find_duplicate_learnings(entries_dir, jaccard_threshold)
    utility_candidates = utility_based_prune_candidates(entries_tuples)
    return duplicates, utility_candidates


def _compute_removal_scores_from_sqlite(
    sqlite_entries: list[LearningEntryDict],
    entries_dir: Path,
    jaccard_threshold: float,
) -> tuple[list[tuple[str, str, float]], list[dict[str, object]]]:
    """Compute removal scores using pre-loaded SQLite entries.

    Variant of ``_compute_removal_scores`` that accepts pre-loaded entry dicts
    (from SQLite) instead of (Path, dict) tuples.

    Args:
        sqlite_entries: Pre-loaded active entry dicts from SQLite.
        entries_dir: Path to the entries directory (for dummy path construction).
        jaccard_threshold: Minimum Jaccard similarity to flag as duplicate.

    Returns:
        Tuple of (duplicates, utility_candidates).
    """
    from trw_mcp.scoring import utility_based_prune_candidates

    duplicates = find_duplicate_learnings(
        entries_dir, jaccard_threshold, entries=sqlite_entries,
    )
    dummy_path = entries_dir / "_dummy.yaml"
    all_entries_tuples: list[tuple[Path, dict[str, object]]] = [
        (dummy_path, cast("dict[str, object]", e)) for e in sqlite_entries
    ]
    utility_candidates = utility_based_prune_candidates(all_entries_tuples)
    return duplicates, utility_candidates


def _select_removal_candidates(
    duplicates: list[tuple[str, str, float]],
    utility_candidates: list[dict[str, object]],
) -> list[tuple[str, str]]:
    """Select the full set of entry IDs and their target statuses for removal.

    Combines Jaccard duplicate IDs (marked "obsolete") with utility-based
    prune candidates (using each candidate's suggested_status), deduplicating
    across both sources so each entry ID appears at most once.

    Args:
        duplicates: List of (older_id, newer_id, similarity) from Jaccard scan.
        utility_candidates: List of candidate dicts from utility_based_prune_candidates.

    Returns:
        List of (entry_id, target_status) pairs where target_status is one of
        "obsolete" or "resolved".
    """
    dedup_ids: set[str] = {older_id for older_id, _newer_id, _sim in duplicates}
    removal: list[tuple[str, str]] = [(rid, "obsolete") for rid in dedup_ids]
    for candidate in utility_candidates:
        cid = str(candidate.get("id", ""))
        if cid and cid not in dedup_ids:
            suggested = str(candidate.get("suggested_status", ""))
            if suggested in ("resolved", "obsolete"):
                removal.append((cid, suggested))
    return removal


def auto_prune_excess_entries(
    trw_dir: Path,
    max_entries: int = 100,
    jaccard_threshold: float = 0.8,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Auto-prune when entries exceed max_entries, with Jaccard dedup.

    PRD-QUAL-012-FR06: Triggered when active entry count > max_entries.
    1. Identifies duplicates via Jaccard similarity
    2. Marks older duplicates as obsolete
    3. If still over limit, prunes lowest-utility entries

    PRD-FIX-033-FR02: Uses SQLite via ``list_entries_by_status`` for entry
    loading instead of YAML glob.  Falls back to YAML on SQLite error.

    Args:
        trw_dir: Path to .trw directory.
        max_entries: Trigger threshold for auto-pruning.
        jaccard_threshold: Minimum similarity for dedup.
        dry_run: If True, report what would be pruned without acting.

    Returns:
        Dict with dedup_candidates, utility_candidates, actions_taken.
    """
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.is_dir():
        return {"dedup_candidates": [], "utility_candidates": [], "actions_taken": 0}

    # PRD-FIX-033-FR02: Try SQLite first, fall back to YAML
    sqlite_entries: list[LearningEntryDict] | None = None
    try:
        from trw_mcp.state.memory_adapter import list_entries_by_status
        sqlite_entries = list_entries_by_status(trw_dir, status="active")
    except Exception:  # broad catch: ImportError + SQLite/adapter failures
        logger.warning("sqlite_read_fallback", step="auto_prune", reason="get_backend failed")

    if sqlite_entries is not None:
        # SQLite path: use pre-loaded entries
        active_count = len(sqlite_entries)

        if active_count <= max_entries:
            return {
                "dedup_candidates": [],
                "utility_candidates": [],
                "actions_taken": 0,
                "active_count": active_count,
                "threshold": max_entries,
            }

        duplicates, utility_candidates = _compute_removal_scores_from_sqlite(
            sqlite_entries, entries_dir, jaccard_threshold,
        )
        removal_pairs = _select_removal_candidates(duplicates, utility_candidates)

        actions = 0
        if not dry_run:
            for rid, suggested in removal_pairs:
                apply_status_update(trw_dir, rid, suggested)
                actions += 1

            if actions > 0:
                resync_learning_index(trw_dir)

        return {
            "dedup_candidates": [
                {"older_id": o, "newer_id": n, "similarity": s}
                for o, n, s in duplicates
            ],
            "utility_candidates": utility_candidates,
            "actions_taken": actions,
            "active_count": active_count,
            "threshold": max_entries,
        }

    # YAML fallback path (original implementation)
    all_entries: list[tuple[Path, dict[str, object]]] = []
    active_count = 0
    for entry_file, data in _ac._iter_entry_files(entries_dir, sorted_order=True):
        all_entries.append((entry_file, data))
        if is_active_entry(data):
            active_count += 1

    if active_count <= max_entries:
        return {
            "dedup_candidates": [],
            "utility_candidates": [],
            "actions_taken": 0,
            "active_count": active_count,
            "threshold": max_entries,
        }

    duplicates, utility_candidates = _compute_removal_scores(
        all_entries, entries_dir, jaccard_threshold,
    )
    removal_pairs = _select_removal_candidates(duplicates, utility_candidates)

    actions = 0
    if not dry_run:
        for rid, suggested in removal_pairs:
            apply_status_update(trw_dir, rid, suggested)
            actions += 1

        if actions > 0:
            resync_learning_index(trw_dir)

    return {
        "dedup_candidates": [
            {"older_id": o, "newer_id": n, "similarity": s}
            for o, n, s in duplicates
        ],
        "utility_candidates": utility_candidates,
        "actions_taken": actions,
        "active_count": active_count,
        "threshold": max_entries,
    }


# ---------------------------------------------------------------------------
# Reflection quality
# ---------------------------------------------------------------------------


def _score_learning_diversity(entries: list[LearningEntryDict]) -> float:
    """Measure tag diversity across a list of learning entries.

    Counts unique tags across all entries and normalises to [0.0, 1.0]
    where 0 tags -> 0.0 and 10+ unique tags -> 1.0.

    Args:
        entries: List of entry dicts, each may contain a ``tags`` list.

    Returns:
        Diversity score in [0.0, 1.0].
    """
    unique_tags: set[str] = set()
    for data in entries:
        tags = data.get("tags", [])
        if isinstance(tags, list):
            unique_tags.update(str(t) for t in tags)
    return min(1.0, len(unique_tags) / 10.0) if unique_tags else 0.0


def _score_learning_depth(
    entries: list[LearningEntryDict],
    total_entries: int,
) -> float:
    """Measure how deeply learnings are being accessed (access ratio).

    Counts entries that have been accessed at least once and divides by
    ``total_entries``.  Returns 0.0 when ``total_entries`` is zero.

    Args:
        entries: List of entry dicts, each may contain an ``access_count`` field.
        total_entries: Total number of entries (denominator for ratio).

    Returns:
        Access ratio in [0.0, 1.0].
    """
    if total_entries == 0:
        return 0.0
    accessed = sum(
        1 for data in entries
        if int(str(data.get("access_count", 0))) > 0
    )
    return accessed / total_entries


def _score_impact_distribution(
    entries: list[LearningEntryDict],
    total_entries: int,
) -> float:
    """Measure Q-learning activation rate across entries.

    Counts entries that have at least one Q-learning observation and
    divides by ``total_entries``.  Returns 0.0 when ``total_entries`` is zero.

    Args:
        entries: List of entry dicts, each may contain a ``q_observations`` field.
        total_entries: Total number of entries (denominator for ratio).

    Returns:
        Q-activation rate in [0.0, 1.0].
    """
    if total_entries == 0:
        return 0.0
    q_activated = sum(
        1 for data in entries
        if int(str(data.get("q_observations", 0))) > 0
    )
    return q_activated / total_entries


def compute_reflection_quality(trw_dir: Path) -> dict[str, object]:
    """Compute composite reflection quality score (0.0-1.0).

    PRD-QUAL-012-FR01: Aggregates multiple signals into a quality score:
    - Reflection count (are reflections happening?)
    - Learnings per reflection (are reflections productive?)
    - Learning diversity (tags, sources -- not all the same type?)
    - Access ratio (are learnings actually being used?)
    - Q-learning activation rate (is the scoring pipeline working?)

    Args:
        trw_dir: Path to .trw directory.

    Returns:
        Dict with score (0.0-1.0), components, and diagnostics.
    """
    cfg_rq: TRWConfig = get_config()
    reflections_dir = trw_dir / cfg_rq.reflections_dir
    entries_dir = _ac._entries_path(trw_dir)

    # Count reflections
    reflection_count = 0
    total_learnings_from_reflections = 0
    reader_rq = FileStateReader()
    if reflections_dir.is_dir():
        for ref_file in reflections_dir.glob("*.yaml"):
            try:
                data = reader_rq.read_yaml(ref_file)
                reflection_count += 1
                new_learnings = data.get("new_learnings", [])
                if isinstance(new_learnings, list):
                    total_learnings_from_reflections += len(new_learnings)
            except (StateError, ValueError, TypeError):
                continue

    # Scan entries for diversity + access + Q-learning metrics
    total_entries = 0
    active_entries = 0
    source_types: set[str] = set()
    # entries_for_metrics: the set of entries passed to sub-metric helpers.
    # SQLite path: active-only (list_active_learnings).
    # YAML path: all entries (original behaviour -- inactive entries counted too).
    entries_for_metrics: list[LearningEntryDict] = []

    _used_sqlite = False
    try:
        from trw_mcp.state.memory_adapter import count_entries, list_active_learnings
        total_entries = count_entries(trw_dir)
        entries_for_metrics = list_active_learnings(trw_dir)
        active_entries = len(entries_for_metrics)
        for entry_data in entries_for_metrics:
            src = str(entry_data.get("source_type", ""))
            if src:
                source_types.add(src)
        _used_sqlite = True
    except Exception:  # broad catch: ImportError + SQLite/adapter failures
        logger.debug("sqlite_fallback_to_yaml", op="collect_learning_metrics")
        # Fall through to YAML

    if not _used_sqlite and entries_dir.is_dir():
        for _path, data in _ac._iter_entry_files(entries_dir):
            total_entries += 1
            if is_active_entry(data):
                active_entries += 1
            entries_for_metrics.append(cast("LearningEntryDict", data))
            src = str(data.get("source_type", ""))
            if src:
                source_types.add(src)

    # Component scores (each 0.0-1.0)
    # 1. Reflection frequency: at least 1 reflection = 0.5, 3+ = 1.0
    reflection_freq = min(1.0, reflection_count / 3.0) if reflection_count > 0 else 0.0

    # 2. Productivity: avg learnings per reflection (0 = 0.0, 2+ = 1.0)
    avg_learnings = (total_learnings_from_reflections / max(reflection_count, 1)
                     if reflection_count > 0 else 0.0)
    productivity = min(1.0, avg_learnings / 2.0)

    # 3. Diversity: tag variety (0 tags = 0.0, 10+ = 1.0)
    diversity = _score_learning_diversity(entries_for_metrics)

    # 4. Access ratio: proportion of entries that have been accessed
    access_ratio = _score_learning_depth(entries_for_metrics, total_entries)

    # 5. Q-learning activation: proportion of entries with Q observations
    q_activation_rate = _score_impact_distribution(entries_for_metrics, total_entries)

    # Weighted composite (reflection_freq 25%, productivity 25%,
    # diversity 15%, access 20%, Q-activation 15%)
    composite = (
        0.25 * reflection_freq
        + 0.25 * productivity
        + 0.15 * diversity
        + 0.20 * access_ratio
        + 0.15 * q_activation_rate
    )

    # Recompute raw counts for diagnostics
    accessed_entries = sum(
        1 for data in entries_for_metrics if int(str(data.get("access_count", 0))) > 0
    )
    q_activated = sum(
        1 for data in entries_for_metrics if int(str(data.get("q_observations", 0))) > 0
    )
    unique_tags: set[str] = set()
    for entry_data in entries_for_metrics:
        tags = entry_data.get("tags", [])
        if isinstance(tags, list):
            unique_tags.update(str(t) for t in tags)
    unique_tags_count = len(unique_tags)

    return {
        "score": round(composite, 3),
        "components": {
            "reflection_frequency": round(reflection_freq, 3),
            "productivity": round(productivity, 3),
            "diversity": round(diversity, 3),
            "access_ratio": round(access_ratio, 3),
            "q_activation_rate": round(q_activation_rate, 3),
        },
        "diagnostics": {
            "reflection_count": reflection_count,
            "avg_learnings_per_reflection": round(avg_learnings, 2),
            "total_entries": total_entries,
            "active_entries": active_entries,
            "accessed_entries": accessed_entries,
            "q_activated_entries": q_activated,
            "unique_tags": unique_tags_count,
            "source_types": sorted(source_types),
        },
    }
