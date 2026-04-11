"""Main consolidation cycle and entry creation — FR03, FR06.

Orchestrates the full consolidation pipeline: cluster detection,
summarization, entry creation, and archival. Includes dry-run mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

import structlog

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state.consolidation._archive import _archive_originals
from trw_mcp.state.consolidation._clustering import find_clusters
from trw_mcp.state.consolidation._clustering import _load_active_entries
from trw_mcp.state.consolidation._summarize import (
    _summarize_cluster_fallback,
    _summarize_cluster_llm,
)
from trw_mcp.state.dedup import cosine_similarity
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# FR03 — Consolidated Entry Creation
# ---------------------------------------------------------------------------


def _create_consolidated_entry(
    cluster: Sequence[LearningEntryDict],
    summary: str,
    detail: str,
    entries_dir: Path,
    writer: FileStateWriter,
) -> dict[str, object]:
    """Create a new consolidated learning entry from a cluster.

    Derives the consolidated entry's fields from the cluster:
    - impact: max of cluster
    - tags: sorted union of all tags
    - evidence: union of all evidence (deduplicated)
    - recurrence: sum of cluster recurrences
    - q_value: max of cluster q_values

    Writes the entry atomically via FileStateWriter.write_yaml.

    Args:
        cluster: List of entry dicts being consolidated.
        summary: Consolidated summary text.
        detail: Consolidated detail text.
        entries_dir: Path to write the new entry YAML.
        writer: FileStateWriter for atomic writes.

    Returns:
        The new consolidated entry dict.
    """
    entry_id = "L-" + uuid4().hex[:8]

    impact = max(float(str(e.get("impact", 0.5))) for e in cluster)

    tags = sorted({str(t) for e in cluster for t in cast("list[object]", e.get("tags") or [])})

    all_evidence: list[str] = list(
        dict.fromkeys(str(ev) for e in cluster for ev in cast("list[object]", e.get("evidence") or []))
    )

    recurrence = sum(int(str(e.get("recurrence", 1))) for e in cluster)
    q_value = max(float(str(e.get("q_value", 0.0))) for e in cluster)

    consolidated_from = [str(e["id"]) for e in cluster if "id" in e]

    entry: dict[str, object] = {
        "id": entry_id,
        "summary": summary,
        "detail": detail,
        "source_type": "consolidated",
        "consolidated_from": consolidated_from,
        "impact": impact,
        "tags": tags,
        "evidence": all_evidence,
        "recurrence": recurrence,
        "q_value": q_value,
        "status": "active",
        "created": datetime.now(tz=timezone.utc).date().isoformat(),
        "updated": datetime.now(tz=timezone.utc).date().isoformat(),
        "last_accessed_at": datetime.now(tz=timezone.utc).date().isoformat(),
    }

    slug = entry_id.replace("/", "-")
    entry_path = entries_dir / f"{slug}.yaml"
    writer.write_yaml(entry_path, entry)

    logger.info(
        "consolidation_entry_created",
        entry_id=entry_id,
        cluster_size=len(cluster),
        consolidated_from=consolidated_from,
    )
    return entry


# ---------------------------------------------------------------------------
# Dry-run helper
# ---------------------------------------------------------------------------


def _mean_pairwise_similarity(cluster: Sequence[LearningEntryDict]) -> float:
    """Compute mean pairwise cosine similarity for dry-run preview.

    Returns 0.0 when embeddings are unavailable or cluster is too small.
    """
    from trw_mcp.state.memory_adapter import embed_text_batch as embed_batch

    if len(cluster) < 2:
        return 0.0

    texts = [str(e.get("summary", "")) + " " + str(e.get("detail", "")) for e in cluster]
    vectors = embed_batch(texts)
    valid: list[list[float]] = [v for v in vectors if v is not None]
    if len(valid) < 2:
        return 0.0

    pairs = [cosine_similarity(valid[i], valid[j]) for i in range(len(valid)) for j in range(i + 1, len(valid))]
    return sum(pairs) / len(pairs) if pairs else 0.0


# ---------------------------------------------------------------------------
# FR06 — Dry-Run Mode + Main Entry Point
# ---------------------------------------------------------------------------


def consolidate_cycle(
    trw_dir: Path,
    *,
    max_entries: int = 50,
    dry_run: bool = False,
    config: TRWConfig | None = None,
) -> dict[str, object]:
    """Run one consolidation cycle across all active learning entries.

    Steps:
    1. Detect clusters via embedding similarity (FR01).
    2. In dry-run mode: return cluster summary without writes (FR06).
    3. For each cluster: summarize via LLM (FR02) or fallback (FR05).
    4. Create consolidated entry (FR03).
    5. Archive originals to cold tier (FR04).

    Args:
        trw_dir: Path to the .trw directory.
        max_entries: Maximum entries to consider for clustering.
        dry_run: If True, skip writes and return cluster preview.
        config: TRWConfig with consolidation thresholds. Uses get_config() if None.

    Returns:
        Dict with consolidation results including cluster count and
        consolidated_count. In dry_run mode: {dry_run: true, clusters: [...],
        consolidated_count: 0}.
    """
    cfg = config or get_config()
    reader = FileStateReader()
    writer = FileStateWriter()

    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    active_entries = _load_active_entries(entries_dir, reader, max_entries=10_000) if entries_dir.exists() else []
    audit_pattern_promotions = detect_audit_finding_recurrence(
        [dict(entry) for entry in active_entries],
        threshold=cfg.audit_pattern_promotion_threshold,
    )
    common_result: dict[str, object] = {
        "audit_pattern_promotions": audit_pattern_promotions,
        "audit_pattern_promotion_threshold": cfg.audit_pattern_promotion_threshold,
    }

    clusters = find_clusters(
        entries_dir,
        reader,
        similarity_threshold=cfg.memory_consolidation_similarity_threshold,
        min_cluster_size=cfg.memory_consolidation_min_cluster,
        max_entries=max_entries,
    )

    if dry_run:
        cluster_previews: list[dict[str, object]] = []
        for cluster in clusters:
            entry_ids = [str(e.get("id", "")) for e in cluster]
            # Compute mean similarity for preview
            mean_sim = _mean_pairwise_similarity(cluster)
            cluster_previews.append(
                {
                    "entry_ids": entry_ids,
                    "count": len(cluster),
                    "mean_similarity": round(mean_sim, 3),
                }
            )
        return {
            **common_result,
            "dry_run": True,
            "clusters": cluster_previews,
            "consolidated_count": 0,
        }

    if not clusters:
        return {
            **common_result,
            "status": "no_clusters",
            "clusters_found": 0,
            "consolidated_count": 0,
        }

    # Load TierManager for cold archival (FR04, graceful if unavailable)
    tier_manager = None
    try:
        from trw_mcp.state.tiers import TierManager as _TierManager

        tier_manager = _TierManager(trw_dir, reader=reader, writer=writer, config=cfg)
    except Exception:  # justified: fail-open, consolidation errors must not block
        # justified: TierManager is optional — consolidation works without cold
        # archival by falling back to status="archived" on the entry.
        logger.warning("consolidation_tier_manager_init_failed", exc_info=True)

    # Instantiate LLM client once for all clusters
    llm: LLMClient | None = None
    try:
        candidate = LLMClient(model="haiku")
        if candidate.available:
            llm = candidate
    except Exception:  # justified: fail-open, consolidation errors must not block
        # justified: LLM is optional — consolidation works without AI summaries
        # via the _summarize_cluster_fallback path.
        logger.warning("consolidation_llm_init_failed", exc_info=True)

    consolidated_count = 0
    errors: list[str] = []

    for cluster in clusters:
        cluster_ids = [str(e.get("id", "")) for e in cluster]
        try:
            # FR02: LLM summarization with FR05 fallback
            llm_result = _summarize_cluster_llm(cluster, llm)
            if llm_result is not None:
                summary = llm_result["summary"]
                detail = llm_result["detail"]
            else:
                fallback = _summarize_cluster_fallback(cluster)
                summary = fallback["summary"]
                detail = fallback["detail"]

            # FR03: Create consolidated entry
            new_entry = _create_consolidated_entry(cluster, summary, detail, entries_dir, writer)
            consolidated_id = str(new_entry["id"])

            # FR04: Archive originals
            _archive_originals(
                cluster,
                consolidated_id,
                entries_dir,
                reader,
                writer,
                tier_manager,
            )
            consolidated_count += 1

        except Exception as exc:  # justified: scan-resilience, one cluster failure must not abort others
            logger.exception(
                "consolidation_cluster_failed",
                cluster_ids=cluster_ids,
                error=str(exc),
            )
            errors.append(f"cluster {cluster_ids}: {exc}")

    result: dict[str, object] = {
        **common_result,
        "status": "completed",
        "clusters_found": len(clusters),
        "consolidated_count": consolidated_count,
    }
    if errors:
        result["errors"] = errors

    logger.info(
        "consolidation_cycle_complete",
        clusters_found=len(clusters),
        consolidated_count=consolidated_count,
        errors=len(errors),
    )
    return result


# ---------------------------------------------------------------------------
# PRD-QUAL-056-FR10 — Audit Pattern Auto-Promotion
# ---------------------------------------------------------------------------

# Known audit-finding category tags.
_AUDIT_FINDING_CATEGORIES: frozenset[str] = frozenset({
    "spec_gap",
    "impl_gap",
    "test_gap",
    "doc_gap",
    "config_gap",
    "security_gap",
    "perf_gap",
    "compat_gap",
})

_PRD_TAG_PREFIX = "PRD-"


def _accumulate_audit_entry(
    entry: dict[str, object],
    category_data: dict[str, dict[str, list[str]]],
) -> None:
    """Accumulate a single entry's audit-finding data into *category_data*.

    Skips entries that are not tagged ``audit-finding`` or lack valid tags.
    """
    raw_tags = entry.get("tags")
    if not raw_tags or not isinstance(raw_tags, list):
        return

    tags: list[str] = [str(t) for t in raw_tags]

    # Only process entries tagged with "audit-finding"
    if "audit-finding" not in tags:
        return

    # Find category tags and PRD tags
    categories: list[str] = []
    prd_ids: list[str] = []
    for tag in tags:
        if tag in _AUDIT_FINDING_CATEGORIES:
            categories.append(tag)
        elif tag.startswith(_PRD_TAG_PREFIX):
            prd_ids.append(tag)

    summary = str(entry.get("summary", ""))

    # Associate each (category, prd_id) pair
    for cat in categories:
        if cat not in category_data:
            category_data[cat] = {}
        for prd_id in prd_ids:
            if prd_id not in category_data[cat]:
                category_data[cat][prd_id] = []
            category_data[cat][prd_id].append(summary)


def detect_audit_finding_recurrence(
    entries: list[dict[str, object]],
    threshold: int = 3,
) -> list[dict[str, object]]:
    """Detect audit-finding learnings that recur across distinct PRDs.

    Scans learnings tagged with ``audit-finding`` and groups by finding
    category (spec_gap, impl_gap, etc.). When a category has findings
    from ``threshold``+ distinct PRD IDs, it is flagged for CLAUDE.md
    promotion.

    Args:
        entries: List of learning entry dicts.
        threshold: Minimum distinct PRD count to trigger promotion flag.

    Returns:
        List of promotion candidate dicts with:
        - category: finding category
        - prd_count: number of distinct PRDs
        - prd_ids: list of PRD IDs
        - sample_summaries: up to 3 representative summaries
        - nudge_line: recommended CLAUDE.md nudge text
    """
    # Group: category -> {prd_id -> [summaries]}
    category_data: dict[str, dict[str, list[str]]] = {}

    for entry in entries:
        _accumulate_audit_entry(entry, category_data)

    # Build promotion candidates
    candidates: list[dict[str, object]] = []
    for category, prd_map in sorted(category_data.items()):
        distinct_prds = len(prd_map)
        if distinct_prds < threshold:
            continue

        prd_ids_sorted = sorted(prd_map.keys())
        # Collect up to 3 sample summaries from distinct PRDs
        sample_summaries: list[str] = []
        for pid in prd_ids_sorted[:3]:
            sums = prd_map[pid]
            if sums:
                sample_summaries.append(sums[0])

        nudge_line = (
            f"Recurring {category.replace('_', ' ')} across {distinct_prds} PRDs"
            f" — consider adding a checklist item"
        )
        # Truncate to 80 chars max
        if len(nudge_line) > 80:
            nudge_line = nudge_line[:77] + "..."

        candidates.append({
            "category": category,
            "prd_count": distinct_prds,
            "prd_ids": prd_ids_sorted,
            "sample_summaries": sample_summaries,
            "nudge_line": nudge_line,
        })

    return candidates
