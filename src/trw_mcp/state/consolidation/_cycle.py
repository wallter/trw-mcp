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
            "dry_run": True,
            "clusters": cluster_previews,
            "consolidated_count": 0,
        }

    if not clusters:
        return {
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
