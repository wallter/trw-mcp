"""Learning analytics and index management — save, update, resync, promote.

Extracted from tools/learning.py (PRD-FIX-010) to separate entry/index
persistence from learning tool logic.

PRD-QUAL-012: Reflection quality scoring, Jaccard dedup, analytics revival.
"""

from __future__ import annotations

import re
import secrets
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterator

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    lock_for_rmw,
    model_to_dict,
)

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()

# Constants
_SLUG_MAX_LEN = 40
_ERROR_KEYWORDS = ("error", "fail", "exception", "crash", "timeout")
_SUCCESS_KEYWORDS = (
    "complete", "success", "pass", "done", "finish",
    "delivered", "approved", "resolved", "merged",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _entries_path(trw_dir: Path) -> Path:
    """Return the canonical entries directory path for a .trw directory."""
    return trw_dir / _config.learnings_dir / _config.entries_dir


def _iter_entry_files(
    entries_dir: Path,
    *,
    sorted_order: bool = False,
) -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield (file_path, data) for each valid YAML entry, skipping index.yaml.

    Silently skips files that fail to parse or have unexpected types.
    """
    files = sorted(entries_dir.glob("*.yaml")) if sorted_order else entries_dir.glob("*.yaml")
    for entry_file in files:
        if entry_file.name == "index.yaml":
            continue
        try:
            data = _reader.read_yaml(entry_file)
            yield entry_file, data
        except (StateError, ValueError, TypeError):
            continue


def _safe_int(data: dict[str, object], key: str, default: int = 0) -> int:
    """Safely extract an integer from a dict with heterogeneous values."""
    return int(str(data.get(key, default)))


def _safe_float(data: dict[str, object], key: str, default: float = 0.0) -> float:
    """Safely extract a float from a dict with heterogeneous values."""
    return float(str(data.get(key, default)))


# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------

def _get_event_type(event: dict[str, object]) -> str:
    """Extract the event type string from an event dict."""
    return str(event.get("event", ""))


def is_error_event(event: dict[str, object]) -> bool:
    """Check if an event represents an error.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates an error or failure.
    """
    event_type = _get_event_type(event).lower()
    return any(kw in event_type for kw in _ERROR_KEYWORDS)


def is_success_event(event: dict[str, object]) -> bool:
    """Check if an event represents a successful outcome.

    Matches events whose type contains success-related keywords such as
    "complete", "success", "pass", "done", "finish", "approved", etc.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates a successful outcome.
    """
    event_type = _get_event_type(event).lower()
    return any(kw in event_type for kw in _SUCCESS_KEYWORDS)


# ---------------------------------------------------------------------------
# Entry lookup
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Event analysis
# ---------------------------------------------------------------------------

def find_repeated_operations(
    events: list[dict[str, object]],
) -> list[tuple[str, int]]:
    """Find operations that were repeated multiple times.

    Args:
        events: List of event dictionaries.

    Returns:
        List of (operation_name, count) tuples, sorted by count descending.
    """
    counts = Counter(
        _get_event_type(event)
        for event in events
        if _get_event_type(event)
    )
    threshold = _config.learning_repeated_op_threshold
    return sorted(
        ((op, count) for op, count in counts.items() if count >= threshold),
        key=lambda x: x[1],
        reverse=True,
    )


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
        event_type = _get_event_type(event) or "unknown"
        success_counts[event_type] = success_counts.get(event_type, 0) + 1
        # Keep the first detail encountered for each type
        data = event.get("data", event.get("detail", ""))
        if data and event_type not in success_details:
            success_details[event_type] = str(data)[:200]

    patterns: list[dict[str, str]] = []
    for event_type, count in sorted(
        success_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        patterns.append({
            "event_type": event_type,
            "summary": f"Success: {event_type} ({count}x)",
            "detail": success_details.get(event_type, ""),
            "count": str(count),
        })

    return patterns[:_config.reflect_max_success_patterns]


def detect_tool_sequences(
    events: list[dict[str, object]],
    lookback: int = 3,
    min_occurrences: int = 3,
) -> list[dict[str, object]]:
    """Detect recurring event sequences that precede success events.

    For each success anchor event, looks back at the preceding ``lookback``
    events, extracts the event_type sequence, and counts occurrences.
    Sequences appearing ``min_occurrences`` or more times are reported.

    Args:
        events: List of event dictionaries from events.jsonl.
        lookback: Number of preceding events to include in each sequence.
        min_occurrences: Minimum occurrences for a sequence to be reported.

    Returns:
        List of dicts with ``sequence`` (list[str]), ``count`` (int),
        and ``success_rate`` (str) keys.
    """
    if len(events) < 2:
        return []

    sequence_counts: dict[tuple[str, ...], int] = {}
    total_anchors = 0

    for i, event in enumerate(events):
        if not is_success_event(event):
            continue
        total_anchors += 1
        start = max(0, i - lookback)
        preceding = [
            str(events[j].get("event", "unknown"))
            for j in range(start, i)
        ]
        current_type = str(event.get("event", "unknown"))
        seq = tuple(preceding + [current_type])
        if len(seq) >= 2:
            sequence_counts[seq] = sequence_counts.get(seq, 0) + 1

    results: list[dict[str, object]] = []
    for seq, count in sorted(
        sequence_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        if count >= min_occurrences:
            rate = f"{count}/{total_anchors}" if total_anchors else "0/0"
            results.append({
                "sequence": list(seq),
                "count": count,
                "success_rate": rate,
            })

    return results


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
    entries_dir = _entries_path(trw_dir)
    if not entries_dir.exists():
        return []

    validated: list[dict[str, object]] = []
    for _path, data in _iter_entry_files(entries_dir, sorted_order=True):
        if str(data.get("status", "active")) != "active":
            continue

        q_value = _safe_float(data, "q_value")
        q_observations = _safe_int(data, "q_observations")

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
    entries_dir = _entries_path(trw_dir)
    if not entries_dir.exists():
        return False

    target = summary_prefix[:50].lower()
    for entry_file in entries_dir.glob("*.yaml"):
        try:
            data = _reader.read_yaml(entry_file)
            existing_summary = str(data.get("summary", ""))[:50].lower()
            if existing_summary == target:
                return True
        except (StateError, ValueError, TypeError):
            continue

    return False


# ---------------------------------------------------------------------------
# Entry persistence
# ---------------------------------------------------------------------------

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
    entry_path = _entries_path(trw_dir) / filename
    _writer.write_yaml(entry_path, model_to_dict(entry))

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
    index_path = trw_dir / _config.learnings_dir / "index.yaml"

    with lock_for_rmw(index_path):
        index_data: dict[str, object] = {}
        if _reader.exists(index_path):
            index_data = _reader.read_yaml(index_path)

        entries_raw = index_data.get("entries", [])
        entries: list[dict[str, object]] = []
        if isinstance(entries_raw, list):
            entries = [e for e in entries_raw if isinstance(e, dict)]

        entries.append({
            "id": entry.id,
            "summary": entry.summary,
            "tags": entry.tags,
            "impact": entry.impact,
            "created": entry.created.isoformat(),
        })

        if len(entries) > _config.learning_max_entries:
            entries.sort(key=lambda e: float(str(e.get("impact", 0.0))))
            entries = entries[-_config.learning_max_entries:]

        index_data["entries"] = entries
        index_data["total_count"] = len(entries)
        _writer.write_yaml(index_path, index_data)


def resync_learning_index(trw_dir: Path) -> None:
    """Rebuild the learning index from all entry files on disk.

    Called after updates to ensure the index stays consistent.

    Args:
        trw_dir: Path to .trw directory.
    """
    entries_dir = _entries_path(trw_dir)
    index_path = trw_dir / _config.learnings_dir / "index.yaml"

    entries: list[dict[str, object]] = []
    if entries_dir.exists():
        for _path, data in _iter_entry_files(entries_dir, sorted_order=True):
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
    _writer.write_yaml(index_path, index_data)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _read_analytics(trw_dir: Path) -> tuple[Path, dict[str, object]]:
    """Read analytics.yaml, returning the path and data dict.

    Creates the context directory if it does not exist.
    """
    context_dir = trw_dir / _config.context_dir
    _writer.ensure_dir(context_dir)
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = {}
    if _reader.exists(analytics_path):
        data = _reader.read_yaml(analytics_path)
    return analytics_path, data


def update_analytics(trw_dir: Path, new_learnings_count: int) -> None:
    """Update .trw/context/analytics.yaml with reflection metrics.

    Args:
        trw_dir: Path to .trw directory.
        new_learnings_count: Number of new learnings produced.
    """
    analytics_path, data = _read_analytics(trw_dir)

    sessions = _safe_int(data, "sessions_tracked") + 1
    total_learnings = _safe_int(data, "total_learnings") + new_learnings_count

    data["sessions_tracked"] = sessions
    data["total_learnings"] = total_learnings
    data["avg_learnings_per_session"] = round(total_learnings / max(sessions, 1), 2)

    _writer.write_yaml(analytics_path, data)


def update_analytics_sync(trw_dir: Path) -> None:
    """Increment CLAUDE.md sync counter in analytics.

    Args:
        trw_dir: Path to .trw directory.
    """
    analytics_path, data = _read_analytics(trw_dir)
    data["claude_md_syncs"] = _safe_int(data, "claude_md_syncs") + 1
    _writer.write_yaml(analytics_path, data)


def update_analytics_extended(
    trw_dir: Path,
    new_learnings_count: int,
    *,
    is_reflection: bool = False,
    is_success: bool = False,
) -> None:
    """Update analytics.yaml with extended metrics (PRD-QUAL-012-FR02/FR03).

    Populates previously dead fields: reflections_completed, success_rate,
    q_learning_activations, high_impact_learnings.

    Args:
        trw_dir: Path to .trw directory.
        new_learnings_count: Number of new learnings produced.
        is_reflection: Whether this call is from a reflection event.
        is_success: Whether this is a successful outcome.
    """
    analytics_path, data = _read_analytics(trw_dir)

    # Core counters (backward compatible with existing update_analytics)
    sessions = _safe_int(data, "sessions_tracked") + 1
    total_learnings = _safe_int(data, "total_learnings") + new_learnings_count
    data["sessions_tracked"] = sessions
    data["total_learnings"] = total_learnings
    data["avg_learnings_per_session"] = round(total_learnings / max(sessions, 1), 2)

    # FR02: Reflection tracking
    if is_reflection:
        data["reflections_completed"] = _safe_int(data, "reflections_completed") + 1

    # FR02: Success rate tracking
    total_outcomes = _safe_int(data, "total_outcomes") + 1
    successes = _safe_int(data, "successful_outcomes")
    if is_success:
        successes += 1
    data["total_outcomes"] = total_outcomes
    data["successful_outcomes"] = successes
    data["success_rate"] = round(successes / max(total_outcomes, 1), 3)

    # FR03: Q-learning activations (scan entries for q_observations > 0)
    entries_dir = _entries_path(trw_dir)
    q_activations = 0
    high_impact = 0
    if entries_dir.is_dir():
        for _path, entry_data in _iter_entry_files(entries_dir):
            if _safe_int(entry_data, "q_observations") > 0:
                q_activations += 1
            if _safe_float(entry_data, "impact", 0.5) >= 0.7:
                high_impact += 1
    data["q_learning_activations"] = q_activations
    data["high_impact_learnings"] = high_impact

    _writer.write_yaml(analytics_path, data)


# ---------------------------------------------------------------------------
# Entry status management
# ---------------------------------------------------------------------------

def mark_promoted(trw_dir: Path, learning_id: str) -> None:
    """Mark a learning entry as promoted to CLAUDE.md.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to mark.
    """
    entries_dir = _entries_path(trw_dir)
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
    entries_dir = _entries_path(trw_dir)
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

    for err in error_events[:max_errors]:
        entry = LearningEntry(
            id=generate_learning_id(),
            summary=f"Error pattern: {err.get('event', 'unknown')}",
            detail=str(err.get("data", err)),
            tags=["error", "auto-discovered"],
            evidence=[str(err.get("ts", ""))],
            impact=0.6,
            source_type="agent",
            source_identity="trw_reflect",
        )
        _save_and_record(trw_dir, entry, new_learnings)

    for op_name, count in repeated_ops[:max_repeated]:
        entry = LearningEntry(
            id=generate_learning_id(),
            summary=f"Repeated operation: {op_name} ({count}x)",
            detail=f"Operation '{op_name}' was repeated {count} times — candidate for scripting",
            tags=["repeated", "optimization"],
            impact=0.5,
            recurrence=count,
            source_type="agent",
            source_identity="trw_reflect",
        )
        _save_and_record(trw_dir, entry, new_learnings)

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
        raw_tags = item.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else ["auto-discovered", "llm"]
        entry = LearningEntry(
            id=generate_learning_id(),
            summary=str(item.get("summary", "LLM-extracted learning")),
            detail=str(item.get("detail", "")),
            tags=tags,
            impact=_safe_float(item, "impact", 0.6),
            source_type="agent",
            source_identity="trw_reflect:llm",
        )
        _save_and_record(trw_dir, entry, new_learnings)

    return new_learnings


# ---------------------------------------------------------------------------
# Dedup and pruning
# ---------------------------------------------------------------------------

def compute_jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings using word tokens.

    PRD-QUAL-012-FR06: Used for dedup detection between learning summaries.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Jaccard index in [0.0, 1.0]. 1.0 means identical token sets.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def find_duplicate_learnings(
    entries_dir: Path,
    threshold: float = 0.8,
) -> list[tuple[str, str, float]]:
    """Find duplicate learning entries by Jaccard similarity on summaries.

    PRD-QUAL-012-FR06: Identifies pairs of active learnings whose summaries
    overlap above the threshold. The older entry in each pair is the
    candidate for dedup (pruning).

    Args:
        entries_dir: Path to entries directory.
        threshold: Minimum Jaccard similarity to flag as duplicate.

    Returns:
        List of (older_id, newer_id, similarity) tuples.
    """
    if not entries_dir.is_dir():
        return []

    active_entries: list[dict[str, object]] = []
    for _path, data in _iter_entry_files(entries_dir, sorted_order=True):
        if str(data.get("status", "active")) == "active":
            active_entries.append(data)

    duplicates: list[tuple[str, str, float]] = []
    for i, entry_a in enumerate(active_entries):
        summary_a = str(entry_a.get("summary", ""))
        for entry_b in active_entries[i + 1:]:
            summary_b = str(entry_b.get("summary", ""))
            sim = compute_jaccard_similarity(summary_a, summary_b)
            if sim >= threshold:
                id_a = str(entry_a.get("id", ""))
                id_b = str(entry_b.get("id", ""))
                duplicates.append((id_a, id_b, round(sim, 3)))
    return duplicates


def auto_prune_excess_entries(
    trw_dir: Path,
    max_entries: int = 100,
    jaccard_threshold: float = 0.8,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Auto-prune when entries exceed max_entries, with Jaccard dedup.

    PRD-QUAL-012-FR06: Triggered when active entry count > max_entries.
    1. Identifies duplicates via Jaccard similarity
    2. Marks older duplicates as obsolete
    3. If still over limit, prunes lowest-utility entries

    Args:
        trw_dir: Path to .trw directory.
        max_entries: Trigger threshold for auto-pruning.
        jaccard_threshold: Minimum similarity for dedup.
        dry_run: If True, report what would be pruned without acting.

    Returns:
        Dict with dedup_candidates, utility_candidates, actions_taken.
    """
    from trw_mcp.scoring import utility_based_prune_candidates

    entries_dir = _entries_path(trw_dir)
    if not entries_dir.is_dir():
        return {"dedup_candidates": [], "utility_candidates": [], "actions_taken": 0}

    all_entries: list[tuple[Path, dict[str, object]]] = []
    active_count = 0
    for entry_file, data in _iter_entry_files(entries_dir, sorted_order=True):
        all_entries.append((entry_file, data))
        if str(data.get("status", "active")) == "active":
            active_count += 1

    if active_count <= max_entries:
        return {"dedup_candidates": [], "utility_candidates": [], "actions_taken": 0,
                "active_count": active_count, "threshold": max_entries}

    # Step 1: Jaccard dedup
    duplicates = find_duplicate_learnings(entries_dir, jaccard_threshold)
    dedup_ids = {older_id for older_id, _newer_id, _sim in duplicates}

    actions = 0
    if not dry_run:
        for dup_id in dedup_ids:
            apply_status_update(trw_dir, dup_id, "obsolete")
            actions += 1

    # Step 2: Utility-based pruning for remaining excess
    utility_candidates = utility_based_prune_candidates(all_entries)

    if not dry_run:
        for candidate in utility_candidates:
            cid = str(candidate.get("id", ""))
            if cid and cid not in dedup_ids:
                suggested = str(candidate.get("suggested_status", ""))
                if suggested in ("resolved", "obsolete"):
                    apply_status_update(trw_dir, cid, suggested)
                    actions += 1

        if actions > 0:
            resync_learning_index(trw_dir)

    return {
        "dedup_candidates": [
            {"older_id": o, "newer_id": n, "similarity": s}
            for o, n, s in duplicates
        ],
        "utility_candidates": utility_candidates,
        "actions_taken": actions,
        "active_count": active_count,
        "threshold": max_entries,
    }


# ---------------------------------------------------------------------------
# Reflection quality
# ---------------------------------------------------------------------------

def compute_reflection_quality(trw_dir: Path) -> dict[str, object]:
    """Compute composite reflection quality score (0.0-1.0).

    PRD-QUAL-012-FR01: Aggregates multiple signals into a quality score:
    - Reflection count (are reflections happening?)
    - Learnings per reflection (are reflections productive?)
    - Learning diversity (tags, sources — not all the same type?)
    - Access ratio (are learnings actually being used?)
    - Q-learning activation rate (is the scoring pipeline working?)

    Args:
        trw_dir: Path to .trw directory.

    Returns:
        Dict with score (0.0-1.0), components, and diagnostics.
    """
    reflections_dir = trw_dir / _config.reflections_dir
    entries_dir = _entries_path(trw_dir)

    # Count reflections
    reflection_count = 0
    total_learnings_from_reflections = 0
    if reflections_dir.is_dir():
        for ref_file in reflections_dir.glob("*.yaml"):
            try:
                data = _reader.read_yaml(ref_file)
                reflection_count += 1
                new_learnings = data.get("new_learnings", [])
                if isinstance(new_learnings, list):
                    total_learnings_from_reflections += len(new_learnings)
            except (StateError, ValueError, TypeError):
                continue

    # Scan entries for diversity + access + Q-learning metrics
    total_entries = 0
    active_entries = 0
    accessed_entries = 0
    q_activated = 0
    unique_tags: set[str] = set()
    source_types: set[str] = set()

    if entries_dir.is_dir():
        for _path, data in _iter_entry_files(entries_dir):
            total_entries += 1
            if str(data.get("status", "active")) == "active":
                active_entries += 1
            if _safe_int(data, "access_count") > 0:
                accessed_entries += 1
            if _safe_int(data, "q_observations") > 0:
                q_activated += 1
            tags = data.get("tags", [])
            if isinstance(tags, list):
                unique_tags.update(str(t) for t in tags)
            src = str(data.get("source_type", ""))
            if src:
                source_types.add(src)

    # Component scores (each 0.0-1.0)
    # 1. Reflection frequency: at least 1 reflection = 0.5, 3+ = 1.0
    reflection_freq = min(1.0, reflection_count / 3.0) if reflection_count > 0 else 0.0

    # 2. Productivity: avg learnings per reflection (0 = 0.0, 2+ = 1.0)
    avg_learnings = (total_learnings_from_reflections / max(reflection_count, 1)
                     if reflection_count > 0 else 0.0)
    productivity = min(1.0, avg_learnings / 2.0)

    # 3. Diversity: tag variety (0 tags = 0.0, 10+ = 1.0)
    diversity = min(1.0, len(unique_tags) / 10.0) if unique_tags else 0.0

    # 4. Access ratio: proportion of entries that have been accessed
    access_ratio = (accessed_entries / max(total_entries, 1)
                    if total_entries > 0 else 0.0)

    # 5. Q-learning activation: proportion of entries with Q observations
    q_activation_rate = (q_activated / max(total_entries, 1)
                         if total_entries > 0 else 0.0)

    # Weighted composite (reflection_freq 25%, productivity 25%,
    # diversity 15%, access 20%, Q-activation 15%)
    composite = (
        0.25 * reflection_freq
        + 0.25 * productivity
        + 0.15 * diversity
        + 0.20 * access_ratio
        + 0.15 * q_activation_rate
    )

    return {
        "score": round(composite, 3),
        "components": {
            "reflection_frequency": round(reflection_freq, 3),
            "productivity": round(productivity, 3),
            "diversity": round(diversity, 3),
            "access_ratio": round(access_ratio, 3),
            "q_activation_rate": round(q_activation_rate, 3),
        },
        "diagnostics": {
            "reflection_count": reflection_count,
            "avg_learnings_per_reflection": round(avg_learnings, 2),
            "total_entries": total_entries,
            "active_entries": active_entries,
            "accessed_entries": accessed_entries,
            "q_activated_entries": q_activated,
            "unique_tags": len(unique_tags),
            "source_types": sorted(source_types),
        },
    }


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
    entries_dir = _entries_path(trw_dir)
    if not entries_dir.is_dir():
        return {"updated_count": 0, "skipped_count": 0, "total_scanned": 0}

    valid_source_types = {"human", "agent"}
    updated = 0
    skipped = 0
    total = 0

    for entry_file, data in _iter_entry_files(entries_dir, sorted_order=True):
        total += 1
        existing = str(data.get("source_type", ""))
        if existing in valid_source_types:
            skipped += 1
            continue
        if not dry_run:
            data["source_type"] = "agent"
            data["source_identity"] = ""
            data["updated"] = date.today().isoformat()
            _writer.write_yaml(entry_file, data)
        updated += 1

    return {
        "updated_count": updated,
        "skipped_count": skipped,
        "total_scanned": total,
        "dry_run": dry_run,
    }
