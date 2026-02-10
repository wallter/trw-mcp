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
