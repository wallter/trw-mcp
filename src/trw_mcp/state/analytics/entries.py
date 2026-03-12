"""Analytics entries — persistence, index management, status, extraction.

Module B of the analytics decomposition.  Handles saving learning entries
to YAML, updating/resyncing the learning index, marking promotions,
applying status updates, and extracting learnings (mechanical + LLM).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import structlog

import trw_mcp.state.analytics.core as _ac
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state._helpers import is_active_entry
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    lock_for_rmw,
    model_to_dict,
)
from trw_mcp.tools._learning_helpers import is_noise_summary

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Learning queries
# ---------------------------------------------------------------------------


def surface_validated_learnings(
    trw_dir: Path,
    q_threshold: float = 0.6,
    cold_start_threshold: int = 3,
) -> list[dict[str, object]]:
    """Surface learnings with high positive Q-values as validated success patterns.

    Scans active learnings for entries with ``q_value >= q_threshold`` and
    ``q_observations >= cold_start_threshold``.

    Args:
        trw_dir: Path to .trw directory.
        q_threshold: Minimum Q-value for inclusion.
        cold_start_threshold: Minimum observation count for inclusion.

    Returns:
        List of dicts with ``learning_id``, ``summary``, ``q_value``,
        ``q_observations``, and ``tags`` keys.
    """
    validated: list[dict[str, object]] = []

    # Primary: read from SQLite via adapter
    try:
        from trw_mcp.state.memory_adapter import list_active_learnings
        all_active = list_active_learnings(trw_dir)
        for data in all_active:
            q_value = float(str(data.get("q_value", 0.0)))
            q_observations = int(str(data.get("q_observations", 0)))
            if q_value >= q_threshold and q_observations >= cold_start_threshold:
                validated.append({
                    "learning_id": str(data.get("id", "")),
                    "summary": str(data.get("summary", "")),
                    "q_value": q_value,
                    "q_observations": q_observations,
                    "tags": data.get("tags", []),
                })
        validated.sort(key=lambda x: float(str(x.get("q_value", 0))), reverse=True)
        return validated
    except Exception:  # broad catch: ImportError + SQLite/adapter failures
        logger.debug("sqlite_fallback_to_yaml", op="surface_validated_learnings")

    # Fallback: YAML scan
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.exists():
        return []

    for _path, data in _ac._iter_entry_files(entries_dir, sorted_order=True):
        if not is_active_entry(data):
            continue

        q_value = _ac._safe_float(data, "q_value")
        q_observations = _ac._safe_int(data, "q_observations")

        if q_value >= q_threshold and q_observations >= cold_start_threshold:
            validated.append({
                "learning_id": str(data.get("id", "")),
                "summary": str(data.get("summary", "")),
                "q_value": q_value,
                "q_observations": q_observations,
                "tags": data.get("tags", []),
            })

    validated.sort(key=lambda x: float(str(x.get("q_value", 0))), reverse=True)
    return validated


def has_existing_success_learning(
    trw_dir: Path,
    summary_prefix: str,
) -> bool:
    """Check if a success learning with the given summary prefix already exists.

    Deduplication check for positive learning generation — prevents
    creating duplicate success pattern learnings across reflection cycles.

    Args:
        trw_dir: Path to .trw directory.
        summary_prefix: First 50 chars of the summary to match against.

    Returns:
        True if a matching learning already exists.
    """
    target = summary_prefix[:50].lower()

    # Check SQLite first, then YAML (entries may exist in either during migration)
    try:
        from trw_mcp.state.memory_adapter import list_active_learnings
        all_active = list_active_learnings(trw_dir)
        for data in all_active:
            if str(data.get("summary", ""))[:50].lower() == target:
                return True
    except Exception:  # broad catch: ImportError + SQLite/adapter failures
        logger.debug("sqlite_fallback_to_yaml", op="has_existing_success_learning")

    # Also check YAML (entries from save_learning_entry may only be in YAML)
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.exists():
        return False

    return any(
        str(data.get("summary", ""))[:50].lower() == target
        for _path, data in _ac._iter_entry_files(entries_dir)
    )


def has_existing_mechanical_learning(
    trw_dir: Path,
    prefix: str,
) -> bool:
    """Check if an active mechanical learning with the given prefix exists.

    Deduplication check for repeated-operation and error-pattern learnings —
    prevents creating duplicate auto-discovered entries across reflection cycles.

    Args:
        trw_dir: Path to .trw directory.
        prefix: Summary prefix to match (e.g. "Repeated operation: file_modified").

    Returns:
        True if a matching active learning already exists.
    """
    # Check SQLite first, then YAML fallback (entries may exist in either during migration)
    try:
        from trw_mcp.state.memory_adapter import list_active_learnings
        all_active = list_active_learnings(trw_dir)
        target = prefix.lower()
        for data in all_active:
            summary = str(data.get("summary", "")).lower()
            if summary.startswith(target):
                return True
    except Exception:  # broad catch: ImportError + SQLite/adapter failures
        logger.debug("sqlite_fallback_to_yaml", op="has_existing_mechanical_learning")

    # Also check YAML (entries from save_learning_entry may only be in YAML)
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.exists():
        return False
    target = prefix.lower()
    for _path, data in _ac._iter_entry_files(entries_dir):
        if not is_active_entry(data):
            continue
        summary = str(data.get("summary", "")).lower()
        if summary.startswith(target):
            return True
    return False


# ---------------------------------------------------------------------------
# Entry persistence
# ---------------------------------------------------------------------------


def save_learning_entry(trw_dir: Path, entry: LearningEntry) -> Path:
    """Save a learning entry to .trw/learnings/entries/ as YAML backup.

    YAML-only: the caller (trw_learn) handles the primary SQLite write
    via memory_adapter.store_learning().  This function writes the YAML
    backup for rollback safety during the migration period.

    QUAL-018 FR03: Infers topic tags from the summary before writing.

    Args:
        trw_dir: Path to .trw directory.
        entry: Learning entry to save.

    Returns:
        Path to the saved YAML entry file.
    """
    # QUAL-018 FR03/FR05: Infer topic tags and append (no duplicates)
    inferred = _ac.infer_topic_tags(entry.summary, entry.tags)
    if inferred:
        entry = entry.model_copy(update={"tags": list(entry.tags) + inferred})

    raw = entry.summary[:_ac._SLUG_MAX_LEN].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    filename = f"{entry.created.isoformat()}-{slug}.yaml"
    entry_path = _ac._entries_path(trw_dir) / filename
    FileStateWriter().write_yaml(entry_path, model_to_dict(entry))
    logger.debug("learning_entry_saved", learning_id=entry.id, path=str(entry_path))

    update_learning_index(trw_dir, entry)
    return entry_path


def update_learning_index(trw_dir: Path, entry: LearningEntry) -> None:
    """Update the learning index with a new entry.

    Uses ``lock_for_rmw`` to prevent concurrent read-modify-write races
    on ``learnings/index.yaml`` when multiple sub-agents write simultaneously.

    Args:
        trw_dir: Path to .trw directory.
        entry: New learning entry to add to index.
    """
    cfg: TRWConfig = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()
    index_path = trw_dir / cfg.learnings_dir / "index.yaml"

    with lock_for_rmw(index_path):
        index_data: dict[str, object] = {}
        if reader.exists(index_path):
            index_data = reader.read_yaml(index_path)

        raw = index_data.get("entries", [])
        entries: list[dict[str, object]] = (
            [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
        )

        entries.append({
            "id": entry.id,
            "summary": entry.summary,
            "tags": entry.tags,
            "impact": entry.impact,
            "created": entry.created.isoformat(),
        })

        if len(entries) > cfg.learning_max_entries:
            entries.sort(key=lambda e: float(str(e.get("impact", 0.0))))
            entries = entries[-cfg.learning_max_entries:]

        index_data["entries"] = entries
        index_data["total_count"] = len(entries)
        writer.write_yaml(index_path, index_data)


def resync_learning_index(trw_dir: Path) -> None:
    """Rebuild the learning index from all entry files on disk.

    Called after updates to ensure the index stays consistent.

    Args:
        trw_dir: Path to .trw directory.
    """
    entries_dir = _ac._entries_path(trw_dir)
    cfg_resync: TRWConfig = get_config()
    index_path = trw_dir / cfg_resync.learnings_dir / "index.yaml"

    entries: list[dict[str, object]] = []
    if entries_dir.exists():
        for _path, data in _ac._iter_entry_files(entries_dir, sorted_order=True):
            entries.append({
                "id": data.get("id", ""),
                "summary": data.get("summary", ""),
                "tags": data.get("tags", []),
                "impact": data.get("impact", 0.5),
                "status": data.get("status", "active"),
                "created": str(data.get("created", "")),
            })

    index_data: dict[str, object] = {
        "entries": entries,
        "total_count": len(entries),
    }
    FileStateWriter().write_yaml(index_path, index_data)


# ---------------------------------------------------------------------------
# Entry status management
# ---------------------------------------------------------------------------


def mark_promoted(trw_dir: Path, learning_id: str) -> None:
    """Mark a learning entry as promoted to CLAUDE.md.

    Updates both SQLite (primary) and YAML (fallback) if available.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to mark.
    """
    # Primary: update in SQLite
    try:
        from trw_mcp.state.memory_adapter import get_backend
        backend = get_backend(trw_dir)
        entry = backend.get(learning_id)
        if entry is not None:
            metadata = dict(entry.metadata) if entry.metadata else {}
            metadata["promoted_to_claude_md"] = "true"
            backend.update(learning_id, metadata=metadata)
    except Exception:  # justified: fail-open, promotion metadata update must not block caller
        logger.debug("promotion_metadata_update_failed", learning_id=learning_id)

    # Fallback: also update YAML if it exists
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.exists():
        return
    found = _ac.find_entry_by_id(entries_dir, learning_id)
    if found is not None:
        entry_file, data = found
        data["promoted_to_claude_md"] = True
        FileStateWriter().write_yaml(entry_file, data)


def apply_status_update(trw_dir: Path, learning_id: str, new_status: str) -> None:
    """Apply a status update to a learning entry on disk.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to update.
        new_status: New status value to set.
    """
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.exists():
        return

    found = _ac.find_entry_by_id(entries_dir, learning_id)
    if found is not None:
        entry_file, data = found
        data["status"] = new_status
        data["updated"] = date.today().isoformat()
        if new_status == LearningStatus.RESOLVED.value:
            data["resolved_at"] = date.today().isoformat()
        FileStateWriter().write_yaml(entry_file, data)


# ---------------------------------------------------------------------------
# Learning extraction (mechanical + LLM)
# ---------------------------------------------------------------------------


def _save_and_record(
    trw_dir: Path,
    entry: LearningEntry,
    results: list[dict[str, str]],
) -> None:
    """Save a learning entry and append its id/summary to results."""
    save_learning_entry(trw_dir, entry)
    results.append({"id": entry.id, "summary": entry.summary})


def extract_learnings_mechanical(
    error_events: list[dict[str, object]],
    repeated_ops: list[tuple[str, int]],
    trw_dir: Path,
    *,
    max_errors: int = 5,
    max_repeated: int = 3,
) -> list[dict[str, str]]:
    """Extract learnings from events using mechanical heuristics (no LLM).

    Processes error patterns into learning entries, saves them to disk,
    and returns summary dicts.  Repeated-operation telemetry is intentionally
    NOT converted to learnings — it stays as analytics data only (PRD-FIX-021).

    Args:
        error_events: Events classified as errors.
        repeated_ops: (operation_name, count) tuples sorted by frequency.
            Accepted for API compatibility but NOT persisted as learnings.
        trw_dir: Path to .trw directory.
        max_errors: Maximum error patterns to extract.
        max_repeated: Unused — kept for API compatibility.

    Returns:
        List of dicts with 'id' and 'summary' keys for each new learning.
    """
    new_learnings: list[dict[str, str]] = []

    for err in error_events[:max_errors]:
        prefix = f"Error pattern: {err.get('event', 'unknown')}"
        if has_existing_mechanical_learning(trw_dir, prefix):
            continue
        entry = LearningEntry(
            id=_ac.generate_learning_id(),
            summary=prefix,
            detail=str(err.get("data", err)),
            tags=["error", "auto-discovered"],
            evidence=[str(err.get("ts", ""))],
            impact=0.6,
            source_type="agent",
            source_identity="trw_reflect",
        )
        _save_and_record(trw_dir, entry, new_learnings)

    # Repeated-ops are tracked as analytics counters only — do NOT create
    # learning entries (PRD-FIX-021: suppress telemetry noise).
    _ = repeated_ops  # acknowledged but intentionally unused

    return new_learnings


def extract_learnings_from_llm(
    llm_items: list[dict[str, object]],
    trw_dir: Path,
) -> list[dict[str, str]]:
    """Convert LLM-extracted learning dicts into persisted LearningEntry objects.

    Filters out telemetry noise (PRD-FIX-021): summaries starting with
    "Repeated operation:" or "Success:" are analytics data, not learnings.

    Args:
        llm_items: List of dicts with summary, detail, tags, impact keys.
        trw_dir: Path to .trw directory.

    Returns:
        List of dicts with 'id' and 'summary' keys for each new learning.
    """
    new_learnings: list[dict[str, str]] = []

    for item in llm_items:
        summary = str(item.get("summary", "LLM-extracted learning"))
        if is_noise_summary(summary):
            continue
        raw_tags = item.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else ["auto-discovered", "llm"]
        entry = LearningEntry(
            id=_ac.generate_learning_id(),
            summary=summary,
            detail=str(item.get("detail", "")),
            tags=tags,
            impact=_ac._safe_float(item, "impact", 0.6),
            source_type="agent",
            source_identity="trw_reflect:llm",
        )
        _save_and_record(trw_dir, entry, new_learnings)

    return new_learnings


# ---------------------------------------------------------------------------
# Source attribution backfill
# ---------------------------------------------------------------------------


def backfill_source_attribution(
    trw_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Backfill missing source_type/source_identity on learning entries.

    Iterates all .yaml entries in .trw/learnings/entries/, sets
    source_type='agent' and source_identity='' on entries missing
    valid source_type.

    Args:
        trw_dir: Path to .trw directory.
        dry_run: If True, count affected entries without modifying files.

    Returns:
        Dict with updated_count, skipped_count, and total_scanned.
    """
    entries_dir = _ac._entries_path(trw_dir)
    if not entries_dir.is_dir():
        return {"updated_count": 0, "skipped_count": 0, "total_scanned": 0}

    valid_source_types = {"human", "agent"}
    updated = 0
    skipped = 0
    total = 0

    for entry_file, data in _ac._iter_entry_files(entries_dir, sorted_order=True):
        total += 1
        existing = str(data.get("source_type", ""))
        if existing in valid_source_types:
            skipped += 1
            continue
        if not dry_run:
            data["source_type"] = "agent"
            data["source_identity"] = ""
            data["updated"] = date.today().isoformat()
            FileStateWriter().write_yaml(entry_file, data)
        updated += 1

    return {
        "updated_count": updated,
        "skipped_count": skipped,
        "total_scanned": total,
        "dry_run": dry_run,
    }
