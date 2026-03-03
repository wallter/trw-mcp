"""Memory consolidation engine — PRD-CORE-044.

Clusters semantically similar learning entries using embeddings and
complete-linkage agglomerative clustering, then consolidates each cluster
into a single entry via LLM summarization (with a longest-entry fallback).
Original entries are archived to the cold tier after consolidation.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import structlog

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state.dedup import cosine_similarity
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from trw_mcp.state.tiers import TierManager

logger = structlog.get_logger()

# NFR06 — Path redaction pattern for LLM prompts
_PATH_RE = re.compile(
    r"(?:/home/|/Users/|/mnt/|/tmp/|/var/|[A-Z]:\\)[^\s,;\"')\]}>]*",
)


def _redact_paths(text: str) -> str:
    """Replace filesystem paths with [REDACTED_PATH] before sending to LLM."""
    return _PATH_RE.sub("[REDACTED_PATH]", text)


# ---------------------------------------------------------------------------
# FR01 — Embedding-Based Cluster Detection
# ---------------------------------------------------------------------------


def find_clusters(
    entries_dir: Path,
    reader: FileStateReader,
    *,
    similarity_threshold: float = 0.75,
    min_cluster_size: int = 3,
    max_entries: int = 50,
) -> list[list[dict[str, object]]]:
    """Detect clusters of semantically similar active learning entries.

    Loads up to *max_entries* active entries from *entries_dir*, generates
    embeddings in a single batch call, then applies complete-linkage
    agglomerative clustering: two entries belong to the same cluster when
    every pair in the group has cosine similarity >= *similarity_threshold*.

    Args:
        entries_dir: Path to the learnings/entries/ directory.
        reader: FileStateReader for loading YAML entry files.
        similarity_threshold: Minimum pairwise similarity to merge into cluster.
        min_cluster_size: Clusters smaller than this are discarded.
        max_entries: Cap on number of entries loaded (sorted alphabetically).

    Returns:
        List of clusters; each cluster is a list of entry dicts.
        Returns [] when embeddings are unavailable.
    """
    from trw_mcp.telemetry.embeddings import embed_batch, embedding_available

    _t0 = time.monotonic()

    if not embedding_available():
        logger.debug("consolidation_embed_unavailable")
        return []

    if not entries_dir.exists():
        return []

    # PRD-FIX-033-FR04: Load active entries from SQLite when available,
    # falling back to YAML glob on error.
    entries: list[dict[str, object]] = []
    _used_sqlite = False
    try:
        from trw_mcp.state.memory_adapter import list_active_learnings
        # Derive trw_dir from entries_dir (entries_dir = trw_dir/learnings/entries)
        trw_dir = entries_dir.parent.parent
        all_active = list_active_learnings(trw_dir, limit=max_entries)
        for data in all_active:
            if len(entries) >= max_entries:
                break
            # Skip already-consolidated entries
            if str(data.get("source_type", "")) == "consolidated":
                continue
            # Skip entries already archived into another consolidation
            if data.get("consolidated_into") is not None:
                continue
            entries.append(data)
        _used_sqlite = True
    except Exception as exc:
        logger.warning(
            "sqlite_read_fallback",
            step="find_clusters",
            reason=str(exc),
        )

    if not _used_sqlite:
        # YAML fallback path (original implementation)
        for yaml_file in sorted(entries_dir.glob("*.yaml")):
            if yaml_file.name == "index.yaml":
                continue
            if len(entries) >= max_entries:
                break
            try:
                data = reader.read_yaml(yaml_file)
            except Exception:  # noqa: BLE001
                continue
            if str(data.get("status", "active")) != "active":
                continue
            # Skip already-consolidated entries
            if str(data.get("source_type", "")) == "consolidated":
                continue
            # Skip entries already archived into another consolidation
            if data.get("consolidated_into") is not None:
                continue
            entries.append(data)

    if len(entries) < min_cluster_size:
        return []

    # Batch embed all entries in one call (FR01 requirement)
    texts = [
        str(e.get("summary", "")) + " " + str(e.get("detail", ""))
        for e in entries
    ]
    vectors = embed_batch(texts)

    # Build (entry, vector) pairs, dropping entries with no embedding
    indexed: list[tuple[dict[str, object], list[float]]] = []
    for i, vec in enumerate(vectors):
        if vec is not None:
            indexed.append((entries[i], vec))

    if len(indexed) < min_cluster_size:
        return []

    # Complete-linkage agglomerative clustering
    # Invariant: every pair within a cluster must be >= threshold
    n = len(indexed)
    cluster_id: list[int] = list(range(n))  # each entry starts in its own cluster

    for i in range(n):
        for j in range(i + 1, n):
            sim = cosine_similarity(indexed[i][1], indexed[j][1])
            if sim >= similarity_threshold:
                # Merge j's cluster into i's cluster if they can be merged
                cid_i = cluster_id[i]
                cid_j = cluster_id[j]
                if cid_i == cid_j:
                    continue
                # Check that ALL pairs between the two clusters satisfy threshold
                i_members = [k for k in range(n) if cluster_id[k] == cid_i]
                j_members = [k for k in range(n) if cluster_id[k] == cid_j]
                can_merge = all(
                    cosine_similarity(indexed[a][1], indexed[b][1]) >= similarity_threshold
                    for a in i_members
                    for b in j_members
                )
                if can_merge:
                    for k in range(n):
                        if cluster_id[k] == cid_j:
                            cluster_id[k] = cid_i

    # Collect clusters by cluster_id
    clusters_map: dict[int, list[dict[str, object]]] = {}
    for idx, cid in enumerate(cluster_id):
        clusters_map.setdefault(cid, []).append(indexed[idx][0])

    result = [
        cluster
        for cluster in clusters_map.values()
        if len(cluster) >= min_cluster_size
    ]

    logger.debug(
        "find_clusters_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        cluster_count=len(result),
        entry_count=len(entries),
    )

    return result


# ---------------------------------------------------------------------------
# FR02 — LLM-Powered Cluster Summarization
# ---------------------------------------------------------------------------


def _summarize_cluster_llm(
    cluster: list[dict[str, object]],
    llm: LLMClient | None = None,
) -> dict[str, str] | None:
    """Summarize a cluster of entries into a single consolidated entry via LLM.

    Builds a prompt containing all cluster entries' summary and detail,
    requests JSON output with "summary" and "detail" keys, and validates
    that the output is shorter than the sum of inputs. Retries once with
    an explicit length constraint if the first response is too long.

    Args:
        cluster: List of entry dicts representing the cluster.
        llm: Optional LLMClient instance. Instantiates one if None.

    Returns:
        Dict with "summary" and "detail" keys, or None on failure.
    """
    client: LLMClient = llm if llm is not None else LLMClient(model="haiku")

    # Build prompt (NFR06: redact filesystem paths before sending to LLM)
    entries_text = "\n".join(
        f"Entry {i + 1}:\n  summary: {_redact_paths(str(e.get('summary', '')))}\n  detail: {_redact_paths(str(e.get('detail', '')))}"
        for i, e in enumerate(cluster)
    )
    prompt = (
        "Consolidate the following related learning entries into a single entry.\n"
        "Respond with exactly one JSON object on a single line:\n"
        '{"summary": "concise one-liner", "detail": "merged explanation"}\n\n'
        + entries_text
    )
    system = "You are a knowledge consolidation assistant. Be concise and precise."

    total_input_len = sum(
        len(str(e.get("summary", ""))) for e in cluster
    )

    response: str | None = client.ask_sync(prompt, system=system)
    if response is None:
        return None

    result = _parse_consolidation_response(response)
    if result is None:
        return None

    # Length check: consolidated summary must be shorter than the sum of inputs
    if len(result["summary"]) < total_input_len:
        return result

    # Retry once with explicit length constraint
    max_chars = max(50, total_input_len // 2)
    retry_prompt = (
        f"{prompt}\n\nIMPORTANT: The summary must be under {max_chars} characters."
    )
    retry_response: str | None = client.ask_sync(retry_prompt, system=system)
    if retry_response is None:
        return None

    return _parse_consolidation_response(retry_response)


def _parse_consolidation_response(response: str) -> dict[str, str] | None:
    """Extract {"summary": ..., "detail": ...} from an LLM response string."""
    for line in response.strip().split("\n"):
        line_s = line.strip()
        if not line_s.startswith("{"):
            continue
        try:
            parsed = json.loads(line_s)
            if "summary" in parsed and "detail" in parsed:
                return {
                    "summary": str(parsed["summary"]),
                    "detail": str(parsed["detail"]),
                }
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# FR03 — Consolidated Entry Creation
# ---------------------------------------------------------------------------


def _create_consolidated_entry(
    cluster: list[dict[str, object]],
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

    tags = sorted({
        str(t)
        for e in cluster
        for t in cast(list[object], e.get("tags") or [])
    })

    all_evidence: list[str] = list(dict.fromkeys(
        str(ev)
        for e in cluster
        for ev in cast(list[object], e.get("evidence") or [])
    ))

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
        "created": date.today().isoformat(),
        "updated": date.today().isoformat(),
        "last_accessed_at": date.today().isoformat(),
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
# FR04 — Original Entry Archival to Cold Tier
# ---------------------------------------------------------------------------


def _archive_originals(
    cluster: list[dict[str, object]],
    consolidated_id: str,
    entries_dir: Path,
    reader: FileStateReader,
    writer: FileStateWriter,
    tier_manager: "TierManager | None" = None,
) -> None:
    """Archive original cluster entries after consolidation.

    For each entry in *cluster*:
    1. Adds ``consolidated_into: <consolidated_id>`` to the entry.
    2. If *tier_manager* is available, calls ``cold_archive(entry_id, path)``.
    3. Otherwise, sets ``status`` to ``"archived"`` (graceful degradation).

    Atomic batch: on any failure, rolls back ``consolidated_into`` writes
    for already-processed entries and deletes the consolidated entry file.
    Logs ERROR on reversion failure.

    Args:
        cluster: Original entry dicts being archived.
        consolidated_id: ID of the newly created consolidated entry.
        entries_dir: Path to the learnings/entries/ directory.
        reader: FileStateReader for loading entry files.
        writer: FileStateWriter for atomic writes.
        tier_manager: Optional TierManager for cold archival.
    """
    processed: list[tuple[Path, dict[str, object]]] = []  # rollback tracking

    for entry in cluster:
        entry_id = str(entry.get("id", ""))
        if not entry_id:
            continue

        # Derive exact filename from entry_id (safe slugify, no glob injection)
        slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
        entry_path = entries_dir / f"{slug}.yaml"
        if not entry_path.exists():
            logger.warning(
                "consolidation_archive_file_not_found",
                entry_id=entry_id,
            )
            continue

        try:
            data = reader.read_yaml(entry_path)
            original_data = dict(data)  # snapshot for rollback

            # Add consolidated_into field
            data["consolidated_into"] = consolidated_id
            writer.write_yaml(entry_path, data)
            processed.append((entry_path, original_data))

            # Archive to cold tier or mark as archived
            if tier_manager is not None and hasattr(tier_manager, "cold_archive"):
                try:
                    tier_manager.cold_archive(entry_id, entry_path)
                except Exception:  # noqa: BLE001
                    # Cold archive failed — mark as archived instead
                    data["status"] = "archived"
                    writer.write_yaml(entry_path, data)
            else:
                data["status"] = "archived"
                writer.write_yaml(entry_path, data)

        except Exception as exc:  # noqa: BLE001
            # Archive failed — rollback all processed entries
            logger.error(
                "consolidation_archive_failed",
                entry_id=entry_id,
                consolidated_id=consolidated_id,
                error=str(exc),
            )
            _rollback_archive(processed, consolidated_id, entries_dir, writer)
            raise

    logger.info(
        "consolidation_archive_complete",
        consolidated_id=consolidated_id,
        archived_count=len(processed),
    )


def _rollback_archive(
    processed: list[tuple[Path, dict[str, object]]],
    consolidated_id: str,
    entries_dir: Path,
    writer: FileStateWriter,
) -> None:
    """Roll back consolidated_into writes on archive failure."""
    for entry_path, original_data in processed:
        try:
            writer.write_yaml(entry_path, original_data)
        except Exception:  # noqa: BLE001
            logger.error(
                "consolidation_rollback_failed",
                path=str(entry_path),
                consolidated_id=consolidated_id,
            )

    # Delete the consolidated entry file
    slug = consolidated_id.replace("/", "-")
    consolidated_path = entries_dir / f"{slug}.yaml"
    try:
        if consolidated_path.exists():
            consolidated_path.unlink()
    except Exception:  # noqa: BLE001
        logger.error(
            "consolidation_rollback_delete_failed",
            consolidated_id=consolidated_id,
        )


# ---------------------------------------------------------------------------
# FR05 — Graceful Degradation Without LLM
# ---------------------------------------------------------------------------


def _summarize_cluster_fallback(
    cluster: list[dict[str, object]],
) -> dict[str, str]:
    """Select the longest-content entry as the consolidated summary/detail.

    Used when LLM is unavailable or summarization fails.
    Logs at INFO level with cluster_size.

    Args:
        cluster: List of entry dicts in the cluster.

    Returns:
        Dict with "summary" and "detail" from the best entry.
    """
    best = max(
        cluster,
        key=lambda e: len(str(e.get("summary", ""))) + len(str(e.get("detail", ""))),
    )
    logger.info(
        "consolidation_llm_fallback",
        cluster_size=len(cluster),
        selected_id=str(best.get("id", "")),
    )
    return {
        "summary": str(best.get("summary", "")),
        "detail": str(best.get("detail", "")),
    }


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
            cluster_previews.append({
                "entry_ids": entry_ids,
                "count": len(cluster),
                "mean_similarity": round(mean_sim, 3),
            })
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
    tier_manager: TierManager | None = None
    try:
        from trw_mcp.state.tiers import TierManager as _TierManager
        tier_manager = _TierManager(trw_dir, reader=reader, writer=writer, config=cfg)
    except Exception:  # noqa: BLE001
        pass  # Graceful degradation — cold archival falls back to status="archived"

    # Instantiate LLM client once for all clusters
    llm: LLMClient | None = None
    try:
        candidate = LLMClient(model="haiku")
        if candidate.available:
            llm = candidate
    except Exception:  # noqa: BLE001
        pass  # LLM is optional — consolidation works without AI summaries

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
            new_entry = _create_consolidated_entry(
                cluster, summary, detail, entries_dir, writer
            )
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

        except Exception as exc:  # noqa: BLE001
            logger.error(
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


def _mean_pairwise_similarity(cluster: list[dict[str, object]]) -> float:
    """Compute mean pairwise cosine similarity for dry-run preview.

    Returns 0.0 when embeddings are unavailable or cluster is too small.
    """
    from trw_mcp.telemetry.embeddings import embed_batch

    if len(cluster) < 2:
        return 0.0

    texts = [
        str(e.get("summary", "")) + " " + str(e.get("detail", ""))
        for e in cluster
    ]
    vectors = embed_batch(texts)
    valid: list[list[float]] = [v for v in vectors if v is not None]
    if len(valid) < 2:
        return 0.0

    pairs = [
        cosine_similarity(valid[i], valid[j])
        for i in range(len(valid))
        for j in range(i + 1, len(valid))
    ]
    return sum(pairs) / len(pairs) if pairs else 0.0
