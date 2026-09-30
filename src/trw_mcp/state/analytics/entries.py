"""Analytics entries — persistence, index management, status, extraction.

Module B of the analytics decomposition.  Handles saving learning entries
to YAML, updating/resyncing the learning index, marking promotions,
applying status updates, and extracting learnings (mechanical + LLM).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import structlog

import trw_mcp.state.analytics.core as _ac
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.learning import (
    LearningEntry,
    LearningStatus,
)
from trw_mcp.state._helpers import is_active_entry
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    lock_for_rmw,
    model_to_dict,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Learning queries
# ---------------------------------------------------------------------------


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

        all_active = list_active_learnings(trw_dir, purpose="maintenance")
        target = prefix.lower()
        for entry in all_active:
            summary = str(entry.get("summary", "")).lower()
            if summary.startswith(target):
                return True
    except Exception:  # justified: boundary, ImportError + SQLite/adapter failures trigger YAML fallback
        logger.warning("sqlite_fallback_to_yaml", op="has_existing_mechanical_learning", exc_info=True)

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


def save_learning_entry(
    trw_dir: Path,
    entry: LearningEntry,
    *,
    index_sink: list[LearningEntry] | None = None,
) -> Path:
    """Save a learning entry to .trw/learnings/entries/ as YAML backup.

    YAML-only: the caller (trw_learn) handles the primary SQLite write
    via memory_adapter.store_learning().  This function writes the YAML
    backup for rollback safety during the migration period.

    QUAL-018 FR03: Infers topic tags from the summary before writing.

    Args:
        trw_dir: Path to .trw directory.
        entry: Learning entry to save.
        index_sink: PRD-FIX-130-FR03 batching seam. When supplied, the entry is
            APPENDED to this list instead of triggering its own read-modify-write
            of ``learnings/index.yaml``; the caller (the journal drain) flushes
            the whole sweep through :func:`update_learning_index_batch` once.
            When ``None`` — every interactive ``trw_learn`` — behaviour is
            byte-identical to the pre-FR03 path.

    Returns:
        Path to the saved YAML entry file.
    """
    # QUAL-018 FR03/FR05: Infer topic tags and append (no duplicates)
    inferred = _ac.infer_topic_tags(entry.summary, entry.tags)
    if inferred:
        entry = entry.model_copy(update={"tags": list(entry.tags) + inferred})

    entries_dir = _ac._entries_path(trw_dir)
    entry_path = entries_dir / _ac.entry_filename(entry.summary, entry.created.isoformat())
    holder = _ac._entry_file_id(entry_path)
    if holder is not None and holder != entry.id:
        # INC-119 h: the plain name is another learning's sidecar. Keep both; never overwrite it.
        entry_path = entries_dir / _ac.entry_filename(entry.summary, entry.created.isoformat(), entry.id)
    FileStateWriter().write_yaml(entry_path, model_to_dict(entry))
    logger.debug("learning_entry_saved", learning_id=entry.id, path=str(entry_path))

    if index_sink is None:
        update_learning_index(trw_dir, entry)
    else:
        index_sink.append(entry)
    return entry_path


def _index_row(entry: LearningEntry) -> dict[str, object]:
    """The index projection of one entry — the ONLY place its shape is defined."""
    return {
        "id": entry.id,
        "summary": entry.summary,
        "tags": entry.tags,
        "impact": entry.impact,
        "created": entry.created.isoformat(),
    }


def update_learning_index(trw_dir: Path, entry: LearningEntry) -> None:
    """Update the learning index with a new entry.

    Uses ``lock_for_rmw`` to prevent concurrent read-modify-write races
    on ``learnings/index.yaml`` when multiple sub-agents write simultaneously.

    Args:
        trw_dir: Path to .trw directory.
        entry: New learning entry to add to index.
    """
    update_learning_index_batch(trw_dir, [entry])


def update_learning_index_batch(trw_dir: Path, entries_to_add: Sequence[LearningEntry]) -> None:
    """Append *entries_to_add* to the learning index in ONE read-modify-write.

    PRD-FIX-130-FR03. The per-record path acquired ``lock_for_rmw`` and rewrote
    the whole 247 KB index once per stored learning — measured at 623-757 ms
    idle and 2.27 s under concurrent boot load, the single dominant cost of a
    journal drain. Draining N records now takes the lock once, reads once,
    appends all N in order, trims once, and writes once.

    TRIM DIVERGENCE, stated rather than hidden: when the batch crosses
    ``learning_max_entries`` the retained set is the ``learning_max_entries``
    highest-impact entries of the COMBINED list. Sequential single-entry updates
    drop the lowest-impact entry after each append and can therefore retain a
    different set. That is a deliberate, documented contract (FR03 invariant c),
    pinned by a test, not an accident. Below the cap the two are identical.

    A failure inside the lock propagates to the caller with the index UNCHANGED
    (the write is the last statement), so entries already stored in the backend
    survive an index failure — they are simply not indexed this sweep.
    """
    if not entries_to_add:
        return
    cfg: TRWConfig = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()
    index_path = trw_dir / cfg.learnings_dir / "index.yaml"

    with lock_for_rmw(index_path):
        index_data: dict[str, object] = {}
        if reader.exists(index_path):
            index_data = reader.read_yaml(index_path)

        raw = index_data.get("entries", [])
        entries: list[dict[str, object]] = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
        entries.extend(_index_row(entry) for entry in entries_to_add)

        if len(entries) > cfg.learning_max_entries:
            entries.sort(key=lambda e: float(str(e.get("impact", 0.0))))
            entries = entries[-cfg.learning_max_entries :]

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
            entries.append(
                {
                    "id": data.get("id", ""),
                    "summary": data.get("summary", ""),
                    "tags": data.get("tags", []),
                    "impact": data.get("impact", 0.5),
                    "status": data.get("status", "active"),
                    "created": str(data.get("created", "")),
                }
            )

    index_data: dict[str, object] = {
        "entries": entries,
        "total_count": len(entries),
    }
    FileStateWriter().write_yaml(index_path, index_data)


# ---------------------------------------------------------------------------
# Entry status management
# ---------------------------------------------------------------------------


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
        data["updated"] = datetime.now(tz=timezone.utc).date().isoformat()
        if new_status == LearningStatus.RESOLVED.value:
            data["resolved_at"] = datetime.now(tz=timezone.utc).date().isoformat()
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
            source_identity="trw_deliver",
        )
        _save_and_record(trw_dir, entry, new_learnings)

    # Repeated-ops are tracked as analytics counters only — do NOT create
    # learning entries (PRD-FIX-021: suppress telemetry noise).
    _ = repeated_ops  # acknowledged but intentionally unused

    return new_learnings
