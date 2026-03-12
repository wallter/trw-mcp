"""Knowledge topology — tag-based clustering for auto-generated topic documents.

Clusters learnings by Jaccard similarity on tag co-occurrence, renders
one Markdown document per cluster, and writes an atomic ``clusters.json``
manifest. Implements PRD-CORE-021 (FR01-FR05, FR08, FR10).
"""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import structlog
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT, DEFAULT_NAMESPACE
from trw_mcp.state.memory_adapter import count_entries, get_backend
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sanitize_slug(name: str) -> str:
    """Normalize a tag name to a filesystem-safe slug.

    Lowercase, spaces to hyphens, strip non-alphanumeric (except hyphens),
    truncate to 64 chars.
    """
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
    return slug[:64]


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not a and not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0  # pragma: no cover — guarded above
    return len(a & b) / union


def _base_result(
    total_count: int,
    config: TRWConfig,
    trw_dir: Path,
    *,
    threshold_met: bool,
    dry_run: bool,
) -> dict[str, object]:
    """Build the common result dict shared by all return paths."""
    return {
        "threshold_met": threshold_met,
        "entry_count": total_count,
        "threshold": config.knowledge_sync_threshold,
        "topics_generated": 0,
        "entries_clustered": 0,
        "output_dir": str(trw_dir / config.knowledge_output_dir),
        "dry_run": dry_run,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Core algorithms
# ---------------------------------------------------------------------------


def build_cooccurrence_matrix(
    entries: list[MemoryEntry],
) -> dict[tuple[str, str], int]:
    """Build tag co-occurrence counts from entry tag sets.

    Tags appearing in fewer than 2 entries are excluded from the matrix.
    """
    tag_freq: Counter[str] = Counter()
    for entry in entries:
        for tag in entry.tags:
            tag_freq[tag] += 1

    valid_tags = {t for t, count in tag_freq.items() if count >= 2}

    matrix: dict[tuple[str, str], int] = {}
    for entry in entries:
        filtered = sorted(set(entry.tags) & valid_tags)
        for i, tag_a in enumerate(filtered):
            for tag_b in filtered[i + 1 :]:
                pair = (tag_a, tag_b)
                matrix[pair] = matrix.get(pair, 0) + 1

    return matrix


def _assign_entries_to_clusters(
    entries: list[MemoryEntry],
    similarity_threshold: float,
) -> list[dict[str, Any]]:
    """Assign each entry to its closest existing cluster or create a new one.

    Iterates over *entries* and computes Jaccard similarity against each
    existing cluster's representative tag set. Entries with no tags are
    skipped. When the best similarity meets *similarity_threshold* the entry
    is added to that cluster (and the cluster's tag set is widened). Otherwise
    a new seed cluster is created.

    Returns the raw internal cluster list (each element has ``tag_set`` and
    ``entry_list`` keys).
    """
    clusters: list[dict[str, Any]] = []

    for entry in entries:
        entry_tags = set(entry.tags)
        if not entry_tags:
            continue

        best_idx = -1
        best_sim = 0.0

        for idx, cluster in enumerate(clusters):
            rep_tags: set[str] = cluster["tag_set"]
            sim = _jaccard(entry_tags, rep_tags)
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        if best_sim >= similarity_threshold and best_idx >= 0:
            clusters[best_idx]["entry_list"].append(entry)
            clusters[best_idx]["tag_set"] |= entry_tags
        else:
            clusters.append({
                "tag_set": set(entry_tags),
                "entry_list": [entry],
            })

    return clusters


def _merge_small_clusters(
    clusters: list[dict[str, Any]],
    min_size: int,
) -> list[dict[str, Any]]:
    """Merge undersized clusters into their closest neighbour, then drop stragglers.

    Repeatedly scans *clusters* in reverse order. When a cluster has fewer
    than *min_size* entries it is merged into the cluster with the highest
    Jaccard similarity and the scan restarts. After no more merges are
    possible, clusters that still fall below *min_size* are removed.
    """
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters) - 1, -1, -1):
            if len(clusters[i]["entry_list"]) >= min_size:
                continue
            best_j = -1
            best_sim = -1.0
            for j in range(len(clusters)):
                if j == i:
                    continue
                sim = _jaccard(clusters[i]["tag_set"], clusters[j]["tag_set"])
                if sim > best_sim:
                    best_sim = sim
                    best_j = j
            if best_j >= 0:
                clusters[best_j]["entry_list"].extend(clusters[i]["entry_list"])
                clusters[best_j]["tag_set"] |= clusters[i]["tag_set"]
                clusters.pop(i)
                merged = True
                break  # Restart after structural change

    # Drop clusters that still don't meet min_size
    return [c for c in clusters if len(c["entry_list"]) >= min_size]


def form_jaccard_clusters(
    entries: list[MemoryEntry],
    threshold: float,
    min_size: int,
) -> list[dict[str, object]]:
    """Cluster entries by Jaccard similarity on their tag sets.

    For each entry, computes Jaccard similarity of its tag set against
    existing cluster representative tag sets and assigns it to the highest-
    similarity cluster above *threshold*. Creates a new seed cluster when
    no match is found.

    After assignment, clusters smaller than *min_size* are merged into the
    closest cluster by Jaccard. Clusters that still do not meet *min_size*
    after the merge attempt are dropped.
    """
    clusters = _assign_entries_to_clusters(entries, threshold)
    clusters = _merge_small_clusters(clusters, min_size)

    # Build output format
    result: list[dict[str, object]] = []
    for cluster in clusters:
        entry_list: list[MemoryEntry] = cluster["entry_list"]
        all_tags: set[str] = cluster["tag_set"]

        tag_counter: Counter[str] = Counter()
        for e in entry_list:
            for t in e.tags:
                tag_counter[t] += 1
        most_common_tag = tag_counter.most_common(1)[0][0] if tag_counter else "cluster"
        slug = sanitize_slug(most_common_tag)

        avg_importance = (
            sum(e.importance for e in entry_list) / len(entry_list)
            if entry_list
            else 0.0
        )

        result.append({
            "slug": slug,
            "tags": sorted(all_tags),
            "entry_ids": [e.id for e in entry_list],
            "entries": entry_list,
            "avg_importance": round(avg_importance, 4),
        })

    return result


# ---------------------------------------------------------------------------
# Manual-marker preservation
# ---------------------------------------------------------------------------


def preserve_manual_markers(existing_content: str, new_content: str) -> str:
    """Re-insert manually curated content from existing file into new render.

    Looks for ``<!-- trw:manual-start -->`` / ``<!-- trw:manual-end -->``
    marker pairs. Content between markers is preserved. Unpaired opening
    markers preserve content from the marker to EOF.

    Returns *existing_content* unchanged on any parse error.
    """
    try:
        normalized = existing_content.replace("\r\n", "\n")

        start_marker = "<!-- trw:manual-start -->"
        end_marker = "<!-- trw:manual-end -->"

        start_idx = normalized.find(start_marker)
        if start_idx == -1:
            return new_content

        end_idx = normalized.find(end_marker, start_idx)
        if end_idx == -1:
            manual_block = normalized[start_idx:]
        else:
            manual_block = normalized[start_idx : end_idx + len(end_marker)]

        return new_content.rstrip("\n") + "\n\n" + manual_block + "\n"
    except (ValueError, IndexError):
        return existing_content


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_topic_document(cluster: dict[str, object]) -> str:
    """Render a Markdown topic document for a cluster."""
    slug = str(cluster.get("slug", "topic"))
    entry_list: list[MemoryEntry] = cast("list[MemoryEntry]", cluster.get("entries", []))
    avg_importance = float(str(cluster.get("avg_importance", 0.0)))
    tags: list[str] = cast("list[str]", cluster.get("tags", []))
    now_iso = datetime.now(timezone.utc).isoformat()

    lines: list[str] = [
        "<!-- trw:auto-generated -->",
        f"# {slug}",
        "",
        f"- **Entries**: {len(entry_list)}",
        f"- **Avg importance**: {avg_importance:.2f}",
        f"- **Last sync**: {now_iso}",
        f"- **Tags**: {', '.join(tags)}",
        "",
        "## Learnings",
        "",
    ]

    for entry in sorted(entry_list, key=lambda e: e.importance, reverse=True):
        summary = entry.content or "(no summary)"
        detail = entry.detail or ""
        if len(detail) > 500:
            detail = detail[:500] + "..."
        evidence_list: list[str] = entry.evidence or []
        entry_tags = entry.tags or []

        lines.append(f"- **{summary}**")
        if detail:
            lines.append(f"  - Detail: {detail}")
        if evidence_list:
            lines.append(f"  - Evidence: {', '.join(evidence_list)}")
        if entry_tags:
            lines.append(f"  - Tags: {', '.join(entry_tags)}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator helpers
# ---------------------------------------------------------------------------


def _render_cluster_documents(
    clusters: list[dict[str, object]],
    entries_map: dict[str, object],  # unused — reserved for future lookup use
) -> tuple[list[dict[str, str]], list[str]]:
    """Render a Markdown document for each cluster.

    Returns ``(documents, errors)`` where *documents* is a list of dicts with
    ``slug`` and ``content`` keys, and *errors* is a list of error strings for
    any clusters that raised during rendering.
    """
    documents: list[dict[str, str]] = []
    errors: list[str] = []
    for cluster in clusters:
        slug = str(cluster.get("slug", "topic"))
        try:
            rendered = render_topic_document(cluster)
        except Exception as exc:  # justified: scan-resilience, one cluster render failure must not abort others
            errors.append(f"Cluster '{slug}': {exc}")
            logger.warning(
                "knowledge_cluster_render_failed",
                cluster_slug=slug,
                error=str(exc),
            )
            continue
        documents.append({"slug": slug, "content": rendered})
    return documents, errors


def _write_knowledge_files(
    documents: list[dict[str, str]],
    knowledge_dir: Path,
    writer: FileStateWriter,
) -> tuple[list[str], int, int, list[str]]:
    """Write rendered topic documents to *knowledge_dir* atomically.

    For each document, reads any existing file to preserve manual markers
    before writing. Accumulates per-cluster errors without aborting the loop.

    Returns ``(cluster_slugs, topics_generated, entries_clustered, errors)``
    where ``cluster_slugs`` is the list of slug keys written (used later for
    ``clusters.json``).

    Note: ``entries_clustered`` is not computable here because the document
    dicts do not carry entry counts. Callers should derive it from the original
    cluster list.
    """
    topics_generated = 0
    errors: list[str] = []
    slugs_written: list[str] = []

    for doc in documents:
        slug = doc["slug"]
        rendered = doc["content"]
        try:
            topic_path = knowledge_dir / f"{slug}.md"
            if topic_path.exists():
                try:
                    existing = topic_path.read_text(encoding="utf-8")
                    rendered = preserve_manual_markers(existing, rendered)
                except OSError:
                    pass  # Best-effort: continue with fresh render

            writer.write_text(topic_path, rendered)
            topics_generated += 1
            slugs_written.append(slug)
        except Exception as exc:  # justified: scan-resilience, one file write failure must not abort others
            errors.append(f"Cluster '{slug}': {exc}")
            logger.warning(
                "knowledge_cluster_render_failed",
                cluster_slug=slug,
                error=str(exc),
            )

    return slugs_written, topics_generated, 0, errors


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def execute_knowledge_sync(
    trw_dir: Path,
    config: TRWConfig,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Orchestrate knowledge topology sync.

    1. Count entries — return early if below threshold (NFR02: fail-open).
    2. If dry_run, return threshold status without writes.
    3. List active entries, form Jaccard clusters.
    4. Render topic documents with manual marker preservation.
    5. Write ``clusters.json`` atomically.
    """
    writer = FileStateWriter()

    # Step 1: threshold check (NFR02: fail-open on StorageError)
    try:
        total_count = count_entries(trw_dir)
    except Exception as exc:  # justified: fail-open, count failure returns safe default rather than crashing
        logger.warning("knowledge_sync_count_failed", error=str(exc))
        result = _base_result(0, config, trw_dir, threshold_met=False, dry_run=dry_run)
        result["errors"] = [f"count_entries failed: {exc}"]
        return result

    threshold_met = total_count >= config.knowledge_sync_threshold

    if not threshold_met:
        logger.info(
            "knowledge_sync_skipped",
            entry_count=total_count,
            threshold=config.knowledge_sync_threshold,
        )
        return _base_result(
            total_count, config, trw_dir, threshold_met=False, dry_run=dry_run,
        )

    if dry_run:
        return _base_result(
            total_count, config, trw_dir, threshold_met=True, dry_run=True,
        )

    # Step 2: list active entries
    backend = get_backend(trw_dir)
    entries = backend.list_entries(
        status=MemoryStatus.ACTIVE,
        namespace=DEFAULT_NAMESPACE,
        limit=DEFAULT_LIST_LIMIT,
    )

    # Step 3: form Jaccard clusters
    clusters = form_jaccard_clusters(
        entries,
        threshold=config.knowledge_jaccard_threshold,
        min_size=config.knowledge_min_cluster_size,
    )

    # Step 4: render and write topic documents
    output_dir = trw_dir / config.knowledge_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build entry-id lookup before rendering (passed to renderer for future use)
    entries_map: dict[str, object] = {
        str(c.get("slug", "")): c.get("entry_ids", []) for c in clusters
    }

    documents, render_errors = _render_cluster_documents(clusters, entries_map)
    slugs_written, topics_generated, _, write_errors = _write_knowledge_files(
        documents, output_dir, writer
    )

    # Tally entries clustered and build cluster_map from original cluster list
    entries_clustered = 0
    errors: list[str] = list(render_errors)
    cluster_map: dict[str, list[str]] = {}
    written_set = set(slugs_written)
    for cluster in clusters:
        slug = str(cluster.get("slug", "topic"))
        entry_ids: list[str] = cast("list[str]", cluster.get("entry_ids", []))
        if slug in written_set:
            entries_clustered += len(entry_ids)
            cluster_map[slug] = entry_ids
            logger.info(
                "knowledge_topic_written",
                slug=slug,
                entry_count=len(entry_ids),
                avg_importance=cluster.get("avg_importance", 0.0),
            )

    errors.extend(write_errors)

    # Step 5: write clusters.json atomically
    clusters_data: dict[str, object] = {
        **cluster_map,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    clusters_path = output_dir / "clusters.json"
    try:
        fd, tmp_path_str = tempfile.mkstemp(dir=str(output_dir), suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(clusters_data, f, indent=2)
            Path(tmp_path_str).replace(clusters_path)
        except Exception:  # broad catch: cleanup temp file on any write failure
            with contextlib.suppress(OSError):
                Path(tmp_path_str).unlink(missing_ok=True)
            raise

        logger.info(
            "knowledge_cache_written",
            cluster_count=len(cluster_map),
            total_entries=entries_clustered,
        )
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"clusters.json write failed: {exc}")
        logger.warning("knowledge_clusters_json_failed", error=str(exc))

    return {
        "threshold_met": True,
        "entry_count": total_count,
        "threshold": config.knowledge_sync_threshold,
        "topics_generated": topics_generated,
        "entries_clustered": entries_clustered,
        "output_dir": str(output_dir),
        "dry_run": False,
        "clusters": list(cluster_map.keys()),
        "errors": errors,
    }
