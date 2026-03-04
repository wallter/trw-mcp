"""Semantic deduplication for learning entries — PRD-CORE-042.

Prevents near-duplicate learnings using embedding cosine similarity.
Three-tier decision: skip (>=0.95), merge (>=0.85), store (<0.85).
Gracefully degrades to no-op when embeddings are unavailable.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple, cast

import structlog

from trw_memory.retrieval.dense import cosine_similarity

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.telemetry.embeddings import embed, embedding_available

logger = structlog.get_logger()

# Re-export so existing importers (tiers.py, consolidation.py) keep working.
__all__ = ["cosine_similarity", "DedupResult", "check_duplicate", "merge_entries", "batch_dedup", "is_migration_needed"]


class DedupResult(NamedTuple):
    """Result of a deduplication check.

    Attributes:
        action: One of "skip", "merge", or "store".
        existing_id: ID of the matched entry (for skip/merge), None for store.
        similarity: Highest cosine similarity found (0.0 when no match).
    """

    action: str  # "skip" | "merge" | "store"
    existing_id: str | None
    similarity: float


def check_duplicate(
    summary: str,
    detail: str,
    entries_dir: Path,
    reader: FileStateReader,
    *,
    config: TRWConfig | None = None,
) -> DedupResult:
    """Check if a new learning is a duplicate of an existing entry.

    Steps:
    1. Generate embedding for ``summary + " " + detail``.
    2. If embedding unavailable → return DedupResult("store", None, 0.0).
    3. Load all active entries from entries_dir.
    4. For each entry, compute cosine similarity with the new embedding.
    5. Return DedupResult based on thresholds from config.

    Args:
        summary: Summary of the new learning.
        detail: Detail of the new learning.
        entries_dir: Path to the entries directory.
        reader: FileStateReader for reading existing entries.
        config: TRWConfig with dedup thresholds. Uses defaults if None.

    Returns:
        DedupResult with action ("skip", "merge", or "store"), existing_id,
        and similarity score.
    """
    _t0 = time.monotonic()
    cfg = config or TRWConfig()

    # Respect embeddings_enabled config — dedup requires embeddings
    if not cfg.embeddings_enabled:
        return DedupResult("store", None, 0.0)

    skip_threshold = cfg.dedup_skip_threshold
    merge_threshold = cfg.dedup_merge_threshold

    # FR06: Validate thresholds — merge must be strictly less than skip
    if merge_threshold >= skip_threshold:
        logger.warning(
            "dedup_threshold_invalid",
            merge=merge_threshold,
            skip=skip_threshold,
        )
        skip_threshold = 0.95
        merge_threshold = 0.85

    # Generate embedding for the new learning
    new_text = summary + " " + detail
    new_vector = embed(new_text)

    if new_vector is None:
        logger.debug("dedup_embed_unavailable", text_len=len(new_text))
        return DedupResult("store", None, 0.0)

    # Load and compare against all active entries
    best_similarity = 0.0
    best_id: str | None = None

    if not entries_dir.exists():
        return DedupResult("store", None, 0.0)

    for yaml_file in sorted(entries_dir.glob("*.yaml")):
        if yaml_file.name == "index.yaml":
            continue
        try:
            data = reader.read_yaml(yaml_file)
        except Exception:
            continue

        # Only compare against active entries
        if str(data.get("status", "active")) != "active":
            continue

        entry_summary = str(data.get("summary", ""))
        entry_detail = str(data.get("detail", ""))
        entry_text = entry_summary + " " + entry_detail

        entry_vector = embed(entry_text)
        if entry_vector is None:
            continue

        sim = cosine_similarity(new_vector, entry_vector)
        if sim > best_similarity:
            best_similarity = sim
            best_id = str(data.get("id", ""))

    # Determine action based on thresholds
    if best_id is not None and best_similarity >= skip_threshold:
        result = DedupResult("skip", best_id, best_similarity)
    elif best_id is not None and best_similarity >= merge_threshold:
        result = DedupResult("merge", best_id, best_similarity)
    else:
        result = DedupResult("store", None, best_similarity)

    logger.debug(
        "dedup_check_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        action=result.action,
        similarity=round(result.similarity, 4),
    )
    return result


def merge_entries(
    existing_path: Path,
    new_entry_data: dict[str, object],
    reader: FileStateReader,
    writer: FileStateWriter,
) -> Path:
    """Merge a new learning into an existing entry.

    Merge strategy:
    - Tags: union of both sets
    - Evidence: union of both sets
    - Impact: max(existing, new)
    - Recurrence: existing + 1
    - Detail: if new detail is longer, append new detail to existing
    - merged_from: append new entry's ID
    - updated: today's date

    Args:
        existing_path: Path to the existing YAML entry file.
        new_entry_data: Dictionary of the new entry's fields.
        reader: FileStateReader for reading the existing entry.
        writer: FileStateWriter for writing the updated entry.

    Returns:
        Path to the updated entry file (same as existing_path).
    """
    existing = reader.read_yaml(existing_path)

    # Tags: union
    raw_existing_tags = existing.get("tags") or []
    raw_new_tags = new_entry_data.get("tags") or []
    existing_tags = [str(t) for t in cast("list[object]", raw_existing_tags)]
    new_tags = [str(t) for t in cast("list[object]", raw_new_tags)]
    merged_tags = list(dict.fromkeys(existing_tags + [t for t in new_tags if t not in existing_tags]))
    existing["tags"] = merged_tags

    # Evidence: union
    raw_existing_ev = existing.get("evidence") or []
    raw_new_ev = new_entry_data.get("evidence") or []
    existing_evidence = [str(e) for e in cast("list[object]", raw_existing_ev)]
    new_evidence = [str(e) for e in cast("list[object]", raw_new_ev)]
    merged_evidence = list(dict.fromkeys(existing_evidence + [e for e in new_evidence if e not in existing_evidence]))
    existing["evidence"] = merged_evidence

    # Impact: max
    existing_impact = float(str(existing.get("impact", 0.5)))
    new_impact = float(str(new_entry_data.get("impact", 0.5)))
    existing["impact"] = max(existing_impact, new_impact)

    # Recurrence: increment
    existing_recurrence = int(str(existing.get("recurrence", 1)))
    existing["recurrence"] = existing_recurrence + 1

    # Detail: append if new detail is longer, with audit trail format (FR03)
    existing_detail = str(existing.get("detail", ""))
    new_detail = str(new_entry_data.get("detail", ""))
    new_id = str(new_entry_data.get("id", "unknown"))
    today = date.today().isoformat()
    if len(new_detail) > len(existing_detail):
        audit_marker = f"\n---\nMerged from {new_id} on {today}:\n"
        if existing_detail:
            existing["detail"] = existing_detail + audit_marker + new_detail
        else:
            existing["detail"] = new_detail

    # merged_from: append new entry ID
    raw_merged_from = existing.get("merged_from") or []
    existing_merged_from = [str(x) for x in cast("list[object]", raw_merged_from)]
    if new_id and new_id not in existing_merged_from:
        existing_merged_from.append(new_id)
    existing["merged_from"] = existing_merged_from

    # updated: today
    existing["updated"] = today

    writer.write_yaml(existing_path, existing)
    logger.debug(
        "dedup_merge_complete",
        existing_id=str(existing.get("id", "")),
        new_id=new_id,
        recurrence=existing["recurrence"],
    )

    # FR03: Re-compute embedding for merged entry and update sqlite-vec if available
    try:
        from trw_mcp.state.memory_store import MemoryStore
        from trw_mcp.telemetry.embeddings import embed as _embed
        if MemoryStore.available():
            merged_text = str(existing.get("summary", "")) + " " + str(existing.get("detail", ""))
            new_embedding = _embed(merged_text)
            if new_embedding is not None:
                from trw_mcp.state._paths import resolve_memory_store_path
                store_path = resolve_memory_store_path()
                store = MemoryStore(store_path)
                try:
                    store.upsert(str(existing.get("id", "")), new_embedding, {})
                finally:
                    store.close()
    except Exception:
        pass  # Best-effort re-indexing

    return existing_path


def is_migration_needed(trw_dir: Path) -> bool:
    """Check if batch dedup migration has been run.

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        True if migration has NOT been run yet (marker missing), False otherwise.
    """
    cfg = get_config()
    marker = trw_dir / cfg.learnings_dir / "dedup_migration.yaml"
    return not marker.exists()


def batch_dedup(
    trw_dir: Path,
    reader: FileStateReader,
    writer: FileStateWriter,
    *,
    config: TRWConfig | None = None,
) -> dict[str, object]:
    """One-time batch deduplication of existing learning entries — FR05.

    Scans all active entries, computes pairwise similarity, merges
    near-duplicates using the same merge strategy as check_duplicate.
    Writes a migration marker when complete.

    Args:
        trw_dir: Path to the .trw directory.
        reader: FileStateReader for reading entry files.
        writer: FileStateWriter for writing updated entry files.
        config: TRWConfig with dedup thresholds. Uses defaults if None.

    Returns:
        Dict with status, entries_scanned, entries_merged, entries_skipped.
    """
    _t0 = time.monotonic()
    cfg = config or get_config()

    # Respect embeddings_enabled config — batch dedup requires embeddings
    if not cfg.embeddings_enabled:
        return {"status": "skipped", "reason": "embeddings not enabled in config"}

    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        return {"status": "skipped", "reason": "no entries directory"}

    if not embedding_available():
        return {"status": "skipped", "reason": "embeddings unavailable"}

    # Load all active entries with their embeddings
    active_entries: list[tuple[Path, dict[str, object], list[float] | None]] = []
    for yaml_file in sorted(entries_dir.glob("*.yaml")):
        if yaml_file.name == "index.yaml":
            continue
        try:
            data = reader.read_yaml(yaml_file)
            if str(data.get("status", "active")) != "active":
                continue
            text = str(data.get("summary", "")) + " " + str(data.get("detail", ""))
            vec = embed(text)
            active_entries.append((yaml_file, data, vec))
        except Exception:
            continue

    merged_count = 0
    skipped_ids: set[str] = set()

    for i in range(len(active_entries)):
        path_i, data_i, vec_i = active_entries[i]
        id_i = str(data_i.get("id", ""))
        if id_i in skipped_ids or vec_i is None:
            continue

        for j in range(i + 1, len(active_entries)):
            path_j, data_j, vec_j = active_entries[j]
            id_j = str(data_j.get("id", ""))
            if id_j in skipped_ids or vec_j is None:
                continue

            sim = cosine_similarity(vec_i, vec_j)

            if sim >= cfg.dedup_skip_threshold:
                # Exact duplicate — mark newer as obsolete
                data_j["status"] = "obsolete"
                data_j["detail"] = (
                    str(data_j.get("detail", ""))
                    + f"\n[Auto-obsoleted: duplicate of {id_i}, similarity={sim:.3f}]"
                )
                writer.write_yaml(path_j, data_j)
                skipped_ids.add(id_j)
                merged_count += 1
            elif sim >= cfg.dedup_merge_threshold:
                # Near-duplicate — merge j into i
                merge_entries(path_i, data_j, reader, writer)
                data_j["status"] = "obsolete"
                data_j["detail"] = (
                    str(data_j.get("detail", ""))
                    + f"\n[Auto-merged into {id_i}, similarity={sim:.3f}]"
                )
                writer.write_yaml(path_j, data_j)
                skipped_ids.add(id_j)
                # Re-read merged data for subsequent comparisons
                try:
                    data_i = reader.read_yaml(path_i)
                    active_entries[i] = (path_i, data_i, vec_i)
                except Exception:
                    pass
                merged_count += 1

    # Write migration marker
    marker = trw_dir / cfg.learnings_dir / "dedup_migration.yaml"
    marker_data: dict[str, object] = {
        "completed": True,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "entries_scanned": len(active_entries),
        "entries_merged": merged_count,
        "entries_unchanged": len(active_entries) - len(skipped_ids),
    }
    writer.write_yaml(marker, marker_data)

    logger.debug(
        "batch_dedup_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        merged=merged_count,
        skipped=len(skipped_ids),
    )

    return {
        "status": "completed",
        "entries_scanned": len(active_entries),
        "entries_merged": merged_count,
        "entries_skipped": len(skipped_ids),
    }
