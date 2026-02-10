"""Learning analytics and index management — save, update, resync, promote.

Extracted from tools/learning.py (PRD-FIX-010) to separate entry/index
persistence from learning tool logic.
"""

from __future__ import annotations

import re
import secrets
from datetime import date
from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()

# Constants
_SLUG_MAX_LEN = 40
_ERROR_KEYWORDS = ("error", "fail", "exception", "crash", "timeout")
_SUCCESS_KEYWORDS = (
    "complete", "success", "pass", "done", "finish",
    "delivered", "approved", "resolved", "merged",
)
_MAX_SUCCESS_PATTERNS = 5


def find_entry_by_id(
    entries_dir: Path,
    learning_id: str,
) -> tuple[Path, dict[str, object]] | None:
    """Find a learning entry file by scanning for a matching ID.

    Args:
        entries_dir: Path to the entries directory.
        learning_id: ID to search for.

    Returns:
        Tuple of (file_path, entry_data) if found, None otherwise.
    """
    for entry_file in entries_dir.glob("*.yaml"):
        try:
            data = _reader.read_yaml(entry_file)
            if data.get("id") == learning_id:
                return entry_file, data
        except (StateError, ValueError, TypeError):
            continue
    return None


def generate_learning_id() -> str:
    """Generate a unique learning entry ID.

    Returns:
        String ID in format 'L-{random_hex}'.
    """
    return f"L-{secrets.token_hex(4)}"


def is_error_event(event: dict[str, object]) -> bool:
    """Check if an event represents an error.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates an error or failure.
    """
    event_type = str(event.get("event", ""))
    return any(kw in event_type.lower() for kw in _ERROR_KEYWORDS)


def find_repeated_operations(
    events: list[dict[str, object]],
) -> list[tuple[str, int]]:
    """Find operations that were repeated multiple times.

    Args:
        events: List of event dictionaries.

    Returns:
        List of (operation_name, count) tuples, sorted by count descending.
    """
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event", ""))
        if event_type:
            counts[event_type] = counts.get(event_type, 0) + 1

    repeated = [
        (op, count) for op, count in counts.items()
        if count >= _config.learning_repeated_op_threshold
    ]
    repeated.sort(key=lambda x: x[1], reverse=True)
    return repeated


def is_success_event(event: dict[str, object]) -> bool:
    """Check if an event represents a successful outcome.

    Matches events whose type contains success-related keywords such as
    "complete", "success", "pass", "done", "finish", "approved", etc.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates a successful outcome.
    """
    event_type = str(event.get("event", "")).lower()
    return any(kw in event_type for kw in _SUCCESS_KEYWORDS)


def find_success_patterns(
    events: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Extract success patterns from events — what worked well.

    Aggregates successful events by type and produces a summary of
    each distinct success pattern found in the event stream.

    Args:
        events: List of event dictionaries from events.jsonl.

    Returns:
        List of dicts with ``event_type``, ``summary``, and ``count`` keys,
        sorted by count descending and capped at ``_MAX_SUCCESS_PATTERNS``.
    """
    success_counts: dict[str, int] = {}
    success_details: dict[str, str] = {}

    for event in events:
        if not is_success_event(event):
            continue
        event_type = str(event.get("event", "unknown"))
        success_counts[event_type] = success_counts.get(event_type, 0) + 1
        # Keep the most recent detail for each type
        data = event.get("data", event.get("detail", ""))
        if data and event_type not in success_details:
            success_details[event_type] = str(data)[:200]

    patterns: list[dict[str, str]] = []
    for event_type, count in sorted(
        success_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        detail = success_details.get(event_type, "")
        patterns.append({
            "event_type": event_type,
            "summary": f"Success: {event_type} ({count}x)",
            "detail": detail,
            "count": str(count),
        })

    return patterns[:_MAX_SUCCESS_PATTERNS]


def save_learning_entry(trw_dir: Path, entry: LearningEntry) -> Path:
    """Save a learning entry to .trw/learnings/entries/.

    Args:
        trw_dir: Path to .trw directory.
        entry: Learning entry to save.

    Returns:
        Path to the saved entry file.
    """
    raw = entry.summary[:_SLUG_MAX_LEN].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    filename = f"{entry.created.isoformat()}-{slug}.yaml"
    entry_path = trw_dir / _config.learnings_dir / _config.entries_dir / filename
    _writer.write_yaml(entry_path, model_to_dict(entry))

    # Update index
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
    from trw_mcp.state.persistence import lock_for_rmw

    index_path = trw_dir / _config.learnings_dir / "index.yaml"

    with lock_for_rmw(index_path):
        index_data: dict[str, object] = {}
        if _reader.exists(index_path):
            index_data = _reader.read_yaml(index_path)

        entries_raw = index_data.get("entries", [])
        entries: list[dict[str, object]] = []
        if isinstance(entries_raw, list):
            entries = [e for e in entries_raw if isinstance(e, dict)]

        # Add new entry summary to index
        entries.append({
            "id": entry.id,
            "summary": entry.summary,
            "tags": entry.tags,
            "impact": entry.impact,
            "created": entry.created.isoformat(),
        })

        # Enforce max entries
        if len(entries) > _config.learning_max_entries:
            # Prune lowest impact entries
            entries.sort(key=lambda e: float(str(e.get("impact", 0.0))))
            entries = entries[-_config.learning_max_entries :]

        index_data["entries"] = entries
        index_data["total_count"] = len(entries)
        _writer.write_yaml(index_path, index_data)


def resync_learning_index(trw_dir: Path) -> None:
    """Rebuild the learning index from all entry files on disk.

    Called after updates to ensure the index stays consistent.

    Args:
        trw_dir: Path to .trw directory.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    index_path = trw_dir / _config.learnings_dir / "index.yaml"

    entries: list[dict[str, object]] = []
    if entries_dir.exists():
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            try:
                data = _reader.read_yaml(entry_file)
                entries.append({
                    "id": data.get("id", ""),
                    "summary": data.get("summary", ""),
                    "tags": data.get("tags", []),
                    "impact": data.get("impact", 0.5),
                    "status": data.get("status", "active"),
                    "created": str(data.get("created", "")),
                })
            except (StateError, ValueError, TypeError):
                continue

    index_data: dict[str, object] = {
        "entries": entries,
        "total_count": len(entries),
    }
    _writer.write_yaml(index_path, index_data)


def update_analytics(trw_dir: Path, new_learnings_count: int) -> None:
    """Update .trw/context/analytics.yaml with reflection metrics.

    Args:
        trw_dir: Path to .trw directory.
        new_learnings_count: Number of new learnings produced.
    """
    context_dir = trw_dir / _config.context_dir
    _writer.ensure_dir(context_dir)
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = {}
    if _reader.exists(analytics_path):
        data = _reader.read_yaml(analytics_path)

    sessions = int(str(data.get("sessions_tracked", 0))) + 1
    total_learnings = int(str(data.get("total_learnings", 0))) + new_learnings_count

    data["sessions_tracked"] = sessions
    data["total_learnings"] = total_learnings
    data["avg_learnings_per_session"] = round(total_learnings / max(sessions, 1), 2)

    _writer.write_yaml(analytics_path, data)


def update_analytics_sync(trw_dir: Path) -> None:
    """Increment CLAUDE.md sync counter in analytics.

    Args:
        trw_dir: Path to .trw directory.
    """
    context_dir = trw_dir / _config.context_dir
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = {}
    if _reader.exists(analytics_path):
        data = _reader.read_yaml(analytics_path)

    data["claude_md_syncs"] = int(str(data.get("claude_md_syncs", 0))) + 1
    _writer.write_yaml(analytics_path, data)


def mark_promoted(trw_dir: Path, learning_id: str) -> None:
    """Mark a learning entry as promoted to CLAUDE.md.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to mark.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    if not entries_dir.exists():
        return

    found = find_entry_by_id(entries_dir, learning_id)
    if found is not None:
        entry_file, data = found
        data["promoted_to_claude_md"] = True
        _writer.write_yaml(entry_file, data)


def extract_learnings_mechanical(
    error_events: list[dict[str, object]],
    repeated_ops: list[tuple[str, int]],
    trw_dir: Path,
    *,
    max_errors: int = 5,
    max_repeated: int = 3,
) -> list[dict[str, str]]:
    """Extract learnings from events using mechanical heuristics (no LLM).

    Processes error patterns and repeated operations into learning entries,
    saves them to disk, and returns summary dicts.

    Args:
        error_events: Events classified as errors.
        repeated_ops: (operation_name, count) tuples sorted by frequency.
        trw_dir: Path to .trw directory.
        max_errors: Maximum error patterns to extract.
        max_repeated: Maximum repeated operations to extract.

    Returns:
        List of dicts with 'id' and 'summary' keys for each new learning.
    """
    new_learnings: list[dict[str, str]] = []

    if error_events:
        for err in error_events[:max_errors]:
            learning_id = generate_learning_id()
            entry = LearningEntry(
                id=learning_id,
                summary=f"Error pattern: {err.get('event', 'unknown')}",
                detail=str(err.get("data", err)),
                tags=["error", "auto-discovered"],
                evidence=[str(err.get("ts", ""))],
                impact=0.6,
            )
            save_learning_entry(trw_dir, entry)
            new_learnings.append({
                "id": learning_id,
                "summary": entry.summary,
            })

    if repeated_ops:
        for op_name, count in repeated_ops[:max_repeated]:
            learning_id = generate_learning_id()
            entry = LearningEntry(
                id=learning_id,
                summary=f"Repeated operation: {op_name} ({count}x)",
                detail=f"Operation '{op_name}' was repeated {count} times — candidate for scripting",
                tags=["repeated", "optimization"],
                impact=0.5,
                recurrence=count,
            )
            save_learning_entry(trw_dir, entry)
            new_learnings.append({
                "id": learning_id,
                "summary": entry.summary,
            })

    return new_learnings


def extract_learnings_from_llm(
    llm_items: list[dict[str, object]],
    trw_dir: Path,
) -> list[dict[str, str]]:
    """Convert LLM-extracted learning dicts into persisted LearningEntry objects.

    Args:
        llm_items: List of dicts with summary, detail, tags, impact keys.
        trw_dir: Path to .trw directory.

    Returns:
        List of dicts with 'id' and 'summary' keys for each new learning.
    """
    new_learnings: list[dict[str, str]] = []

    for item in llm_items:
        learning_id = generate_learning_id()
        raw_tags = item.get("tags", "")
        parsed_tags: list[str] = (
            raw_tags if isinstance(raw_tags, list) else ["auto-discovered", "llm"]
        )
        entry = LearningEntry(
            id=learning_id,
            summary=str(item.get("summary", "LLM-extracted learning")),
            detail=str(item.get("detail", "")),
            tags=parsed_tags,
            impact=float(str(item.get("impact", 0.6))),
        )
        save_learning_entry(trw_dir, entry)
        new_learnings.append({
            "id": learning_id,
            "summary": entry.summary,
        })

    return new_learnings


def apply_status_update(trw_dir: Path, learning_id: str, new_status: str) -> None:
    """Apply a status update to a learning entry on disk.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to update.
        new_status: New status value to set.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    if not entries_dir.exists():
        return

    found = find_entry_by_id(entries_dir, learning_id)
    if found is not None:
        entry_file, data = found
        data["status"] = new_status
        data["updated"] = date.today().isoformat()
        if new_status == LearningStatus.RESOLVED.value:
            data["resolved_at"] = date.today().isoformat()
        _writer.write_yaml(entry_file, data)
