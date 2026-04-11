"""Semantic deduplication for learning entries — PRD-CORE-042.

Prevents near-duplicate learnings using embedding cosine similarity.
Three-tier decision: skip (>=0.95), merge (>=0.85), store (<0.85).

Uses sqlite-vec KNN search when available (sub-ms); falls back to
linear YAML scan when the backend is unavailable.

Obsolete entries are checked for skip (>=0.95) but NOT for merge,
preventing runaway re-learning of content surfaced by session_start.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, cast

import structlog
from trw_memory.retrieval.dense import cosine_similarity

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.typed_dicts import BatchDedupResult
from trw_mcp.state._helpers import iter_yaml_entry_files
from trw_mcp.state.memory_adapter import embed_text as embed
from trw_mcp.state.memory_adapter import embedding_available
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

# Re-export so existing importers (tiers.py, consolidation.py) keep working.
__all__ = ["DedupResult", "batch_dedup", "check_duplicate", "cosine_similarity", "is_migration_needed", "merge_entries"]


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


def _distance_to_similarity(distance: float) -> float:
    """Convert sqlite-vec L2 distance to cosine similarity.

    For unit-normalized vectors (which all-MiniLM-L6-v2 produces via
    ``normalize_embeddings=True``), the relationship is:
    ``distance² = 2 * (1 - cosine_similarity)``
    so ``cosine_similarity = 1 - distance² / 2``.
    """
    return 1.0 - (distance * distance) / 2.0


def _check_duplicate_via_backend(
    new_vector: list[float],
    trw_dir: Path,
    skip_threshold: float,
    merge_threshold: float,
) -> DedupResult | None:
    """Try KNN dedup via the sqlite-vec backend (fast path).

    Returns a DedupResult if the backend is available and produces a
    definitive answer, or None to signal the caller should fall back
    to the YAML linear scan.

    Obsolete/resolved entries trigger ``skip`` (>= skip_threshold) but
    never ``merge``, preventing runaway re-learning of content that was
    already recorded and later obsoleted.
    """
    try:
        from trw_mcp.state.memory_adapter import get_backend

        backend = get_backend(trw_dir)
        # Ask for more candidates than we strictly need so we can
        # filter by status and still find the best match.
        hits = backend.search_vectors(new_vector, top_k=10)
        if not hits:
            return None  # No vectors indexed yet — fall back to YAML

        best_similarity = 0.0
        best_id: str | None = None
        best_is_active = False

        for entry_id, distance in hits:
            sim = _distance_to_similarity(distance)
            if sim <= best_similarity:
                continue

            entry = backend.get(entry_id)
            if entry is None:
                continue

            is_active = str(entry.status.value if hasattr(entry.status, "value") else entry.status) == "active"

            best_similarity = sim
            best_id = entry_id
            best_is_active = is_active

        if best_id is not None and best_similarity >= skip_threshold:
            # Skip against both active AND obsolete entries
            return DedupResult("skip", best_id, best_similarity)
        if best_id is not None and best_similarity >= merge_threshold and best_is_active:
            # Merge only into active entries
            return DedupResult("merge", best_id, best_similarity)

        return DedupResult("store", None, best_similarity)

    except Exception:  # justified: fail-open, backend dedup availability falls back to YAML heuristics
        logger.debug("dedup_backend_unavailable_fallback_to_yaml", exc_info=True)
        return None


def check_duplicate(
    summary: str,
    detail: str,
    entries_dir: Path,
    reader: FileStateReader,
    *,
    config: TRWConfig | None = None,
) -> DedupResult:
    """Check if a new learning is a duplicate of an existing entry.

    Two-tier strategy:
    1. **Fast path** (sqlite-vec): KNN search via the memory backend.
       Sub-millisecond, status-agnostic — filters status *after* retrieval.
    2. **Fallback** (YAML scan): Linear scan of entry files when the
       backend is unavailable.

    In both paths, obsolete/resolved entries trigger ``skip`` (>= 0.95)
    but never ``merge``, preventing the runaway re-learning loop where
    session_start injects content → agent re-learns it → deliver
    obsoletes it → next session repeats.

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

    # --- Fast path: sqlite-vec KNN search ---
    # Resolve .trw dir from entries_dir (entries_dir = trw_dir / learnings / entries)
    trw_dir = entries_dir.parent.parent
    backend_result = _check_duplicate_via_backend(
        new_vector, trw_dir, skip_threshold, merge_threshold
    )
    if backend_result is not None:
        logger.debug(
            "dedup_check_complete",
            duration_ms=round((time.monotonic() - _t0) * 1000, 2),
            action=backend_result.action,
            similarity=round(backend_result.similarity, 4),
            path="backend",
        )
        return backend_result

    # --- Fallback: YAML linear scan ---
    best_similarity = 0.0
    best_id: str | None = None
    best_is_active = False

    if not entries_dir.exists():
        return DedupResult("store", None, 0.0)

    for yaml_file in iter_yaml_entry_files(entries_dir):
        if yaml_file.name == "index.yaml":
            continue
        try:
            data = reader.read_yaml(yaml_file)
        except (OSError, StateError):
            continue

        entry_status = str(data.get("status", "active"))
        is_active = entry_status == "active"

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
            best_is_active = is_active

    # Determine action based on thresholds + status
    if best_id is not None and best_similarity >= skip_threshold:
        # Skip against both active AND obsolete entries
        result = DedupResult("skip", best_id, best_similarity)
    elif best_id is not None and best_similarity >= merge_threshold and best_is_active:
        # Merge only into active entries
        result = DedupResult("merge", best_id, best_similarity)
    else:
        result = DedupResult("store", None, best_similarity)

    logger.debug(
        "dedup_check_complete",
        duration_ms=round((time.monotonic() - _t0) * 1000, 2),
        action=result.action,
        similarity=round(result.similarity, 4),
        path="yaml_fallback",
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
    today = datetime.now(tz=timezone.utc).date().isoformat()
    if len(new_detail) > len(existing_detail):
        audit_marker = f"\n---\nMerged from {new_id} on {today}:\n"
        if existing_detail:
            existing["detail"] = existing_detail + audit_marker + new_detail
        else:
            existing["detail"] = new_detail

    # Assertions: union by (type, pattern, target) tuple (PRD-CORE-086 FR05)
    raw_existing_assertions = existing.get("assertions") or []
    raw_new_assertions = new_entry_data.get("assertions") or []
    if raw_new_assertions and isinstance(raw_new_assertions, list):
        existing_assertions = (
            list(raw_existing_assertions) if isinstance(raw_existing_assertions, list) else []
        )
        seen_keys: set[tuple[str, str, str]] = set()
        for a in existing_assertions:
            if isinstance(a, dict):
                seen_keys.add(
                    (str(a.get("type", "")), str(a.get("pattern", "")), str(a.get("target", "")))
                )
        for a in raw_new_assertions:
            if isinstance(a, dict):
                key = (
                    str(a.get("type", "")),
                    str(a.get("pattern", "")),
                    str(a.get("target", "")),
                )
                if key not in seen_keys:
                    existing_assertions.append(a)
                    seen_keys.add(key)
        existing["assertions"] = existing_assertions

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
        from trw_mcp.state.memory_adapter import embed_text as _embed
        from trw_mcp.state.memory_store import MemoryStore

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
    except (ImportError, OSError, ValueError):
        logger.debug("dedup_reindex_skipped", exc_info=True)  # justified: fail-open, best-effort re-indexing

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
) -> BatchDedupResult:
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
        return BatchDedupResult(status="skipped", reason="embeddings not enabled in config")

    entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
    if not entries_dir.exists():
        return BatchDedupResult(status="skipped", reason="no entries directory")

    if not embedding_available():
        return BatchDedupResult(status="skipped", reason="embeddings unavailable")

    # Load all active entries with their embeddings
    active_entries: list[tuple[Path, dict[str, object], list[float] | None]] = []
    for yaml_file in iter_yaml_entry_files(entries_dir):
        try:
            data = reader.read_yaml(yaml_file)
            if str(data.get("status", "active")) != "active":
                continue
            text = str(data.get("summary", "")) + " " + str(data.get("detail", ""))
            vec = embed(text)
            active_entries.append((yaml_file, data, vec))
        except (OSError, StateError, ValueError):
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
                    str(data_j.get("detail", "")) + f"\n[Auto-obsoleted: duplicate of {id_i}, similarity={sim:.3f}]"
                )
                writer.write_yaml(path_j, data_j)
                skipped_ids.add(id_j)
                merged_count += 1
            elif sim >= cfg.dedup_merge_threshold:
                # Near-duplicate — merge j into i
                merge_entries(path_i, data_j, reader, writer)
                data_j["status"] = "obsolete"
                data_j["detail"] = str(data_j.get("detail", "")) + f"\n[Auto-merged into {id_i}, similarity={sim:.3f}]"
                writer.write_yaml(path_j, data_j)
                skipped_ids.add(id_j)
                # Re-read merged data for subsequent comparisons
                try:
                    data_i = reader.read_yaml(path_i)
                    active_entries[i] = (path_i, data_i, vec_i)
                except (OSError, StateError):
                    logger.debug("merged_entry_reread_failed", path=str(path_i), exc_info=True)
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

    return BatchDedupResult(
        status="completed",
        entries_scanned=len(active_entries),
        entries_merged=merged_count,
        entries_skipped=len(skipped_ids),
    )
