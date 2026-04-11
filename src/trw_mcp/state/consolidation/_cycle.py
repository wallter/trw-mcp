"""Main consolidation cycle and entry creation — FR03, FR06.

Orchestrates the full consolidation pipeline: cluster detection,
summarization, entry creation, and archival. Includes dry-run mode.
"""

from __future__ import annotations

import re
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
from trw_mcp.state._helpers import truncate_nudge_line
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
    "integration_gap",
    "traceability_gap",
})

_PRD_TAG_PREFIX = "PRD-"
_AUDIT_PATTERN_STOPWORDS: frozenset[str] = frozenset({
    "a",
    "an",
    "and",
    "another",
    "audit",
    "audits",
    "detected",
    "finding",
    "findings",
    "for",
    "from",
    "in",
    "into",
    "is",
    "missing",
    "not",
    "of",
    "on",
    "same",
    "surfaced",
    "the",
    "this",
    "to",
    "with",
})
_AUDIT_PATTERN_SYNONYMS: dict[str, str] = {
    "callsite": "callsite",
    "callsites": "callsite",
    "hook": "wiring",
    "hookup": "wiring",
    "hookups": "wiring",
    "implementation": "implementation",
    "implementations": "implementation",
    "integrate": "integration",
    "integrated": "integration",
    "integration": "integration",
    "matrixes": "matrix",
    "matrices": "matrix",
    "miss": "missing",
    "missed": "missing",
    "regressions": "regression",
    "tests": "test",
    "trace": "traceability",
    "traces": "traceability",
    "traceability": "traceability",
    "wired": "wiring",
    "wire": "wiring",
    "wireup": "wiring",
    "wiring": "wiring",
}
_AUDIT_PATTERN_PREVENTION_STRATEGIES: dict[str, str] = {
    "spec_gap": "Require FR-by-FR spec reconciliation before review sign-off.",
    "impl_gap": "Verify the production call path and integration wiring before closing remediation.",
    "test_gap": "Add requirement-linked regression coverage before marking the fix complete.",
    "integration_gap": "Exercise end-to-end integration points, not just isolated units, before delivery.",
    "traceability_gap": "Update traceability artifacts alongside code changes before delivery sign-off.",
}


def _normalize_audit_pattern_token(token: str) -> str:
    """Normalize a token from an audit summary for recurrence grouping."""
    normalized = _AUDIT_PATTERN_SYNONYMS.get(token, token)
    if normalized.endswith("ies") and len(normalized) > 4:
        normalized = normalized[:-3] + "y"
    elif normalized.endswith("s") and len(normalized) > 4 and not normalized.endswith("ss"):
        normalized = normalized[:-1]
    return normalized


def _normalize_audit_pattern(summary: str) -> str:
    """Reduce a free-text audit summary to a stable recurrence key."""
    raw_tokens = re.findall(r"[a-z0-9]+", summary.lower())
    normalized_tokens: list[str] = []
    for token in raw_tokens:
        if token.startswith("prd"):
            continue
        normalized = _normalize_audit_pattern_token(token)
        if normalized in _AUDIT_PATTERN_STOPWORDS or len(normalized) <= 2:
            continue
        normalized_tokens.append(normalized)

    unique_tokens = sorted(dict.fromkeys(normalized_tokens))
    if unique_tokens:
        return " ".join(unique_tokens[:6])

    fallback = re.sub(r"\s+", " ", summary.strip().lower())
    return fallback[:80]


def _build_audit_pattern_summary(sample_summaries: list[str], normalized_pattern: str) -> str:
    """Build a human-readable pattern summary from sample summaries."""
    if sample_summaries:
        return sample_summaries[0]
    return normalized_pattern.replace("_", " ").strip()


def _build_audit_synthesized_summary(
    category: str,
    pattern_summary: str,
    prd_count: int,
    prevention_strategy: str,
) -> str:
    """Build the FR10-required synthesized summary."""
    category_name = category.replace("_", " ")
    return (
        f"Recurring {category_name} pattern: {pattern_summary}. "
        f"Observed across {prd_count} PRDs. Prevention: {prevention_strategy}"
    )


def _accumulate_audit_entry(
    entry: dict[str, object],
    pattern_data: dict[tuple[str, str], dict[str, list[str]]],
) -> None:
    """Accumulate a single entry's audit-finding data into *pattern_data*.

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
    normalized_pattern = _normalize_audit_pattern(summary)

    for cat in categories:
        key = (cat, normalized_pattern)
        if key not in pattern_data:
            pattern_data[key] = {}
        for prd_id in prd_ids:
            if prd_id not in pattern_data[key]:
                pattern_data[key][prd_id] = []
            pattern_data[key][prd_id].append(summary)


def detect_audit_finding_recurrence(
    entries: list[dict[str, object]],
    threshold: int = 3,
) -> list[dict[str, object]]:
    """Detect audit-finding learnings that recur across distinct PRDs.

    Scans learnings tagged with ``audit-finding`` and groups by normalized
    pattern summary within a root-cause category. When the same pattern
    recurs across ``threshold``+ distinct PRD IDs, it is flagged for
    CLAUDE.md promotion.

    Args:
        entries: List of learning entry dicts.
        threshold: Minimum distinct PRD count to trigger promotion flag.

    Returns:
        List of promotion candidate dicts with:
        - category: finding category
        - normalized_pattern: normalized recurrence key
        - pattern_summary: representative human-readable pattern summary
        - prd_count: number of distinct PRDs
        - prd_ids: list of PRD IDs
        - sample_summaries: up to 3 representative summaries
        - synthesized_summary: FR10-required synthesized promotion summary
        - prevention_strategy: recommended prevention strategy
        - nudge_line: recommended CLAUDE.md nudge text
    """
    pattern_data: dict[tuple[str, str], dict[str, list[str]]] = {}

    for entry in entries:
        _accumulate_audit_entry(entry, pattern_data)

    candidates: list[dict[str, object]] = []
    for (category, normalized_pattern), prd_map in sorted(pattern_data.items()):
        distinct_prds = len(prd_map)
        if distinct_prds < threshold:
            continue

        prd_ids_sorted = sorted(prd_map.keys())
        sample_summaries: list[str] = []
        for pid in prd_ids_sorted[:3]:
            sums = prd_map[pid]
            if sums:
                sample_summaries.append(sums[0])

        pattern_summary = _build_audit_pattern_summary(sample_summaries, normalized_pattern)
        prevention_strategy = _AUDIT_PATTERN_PREVENTION_STRATEGIES.get(
            category,
            "Add an explicit prevention checklist item before delivery sign-off.",
        )
        synthesized_summary = _build_audit_synthesized_summary(
            category,
            pattern_summary,
            distinct_prds,
            prevention_strategy,
        )
        nudge_line = truncate_nudge_line(
            f"Recurring {category.replace('_', ' ')}: {pattern_summary}",
        )

        candidates.append({
            "category": category,
            "normalized_pattern": normalized_pattern,
            "pattern_summary": pattern_summary,
            "prd_count": distinct_prds,
            "prd_ids": prd_ids_sorted,
            "sample_summaries": sample_summaries,
            "synthesized_summary": synthesized_summary,
            "prevention_strategy": prevention_strategy,
            "nudge_line": nudge_line,
        })

    return candidates
