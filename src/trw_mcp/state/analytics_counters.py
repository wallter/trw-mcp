"""Analytics counters — counter updates and event pattern detection.

Module C of the analytics decomposition.  Handles analytics.yaml counter
updates (sessions, learnings, reflections, success rate, Q-activations)
and event analysis (repeated operations, success patterns, tool sequences).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import structlog

import trw_mcp.state.analytics_core as _ac
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()


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
        et
        for event in events
        if (et := _ac._get_event_type(event))
    )
    cfg: TRWConfig = get_config()
    threshold = cfg.learning_repeated_op_threshold
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
        if not _ac.is_success_event(event):
            continue
        event_type = _ac._get_event_type(event) or "unknown"
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

    cfg_sp: TRWConfig = get_config()
    return patterns[:cfg_sp.reflect_max_success_patterns]


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
        if not _ac.is_success_event(event):
            continue
        total_anchors += 1
        start = max(0, i - lookback)
        preceding = [
            _ac._get_event_type(events[j]) or "unknown"
            for j in range(start, i)
        ]
        current_type = _ac._get_event_type(event) or "unknown"
        seq = tuple([*preceding, current_type])
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
# Analytics counter management
# ---------------------------------------------------------------------------


def _read_analytics(trw_dir: Path) -> tuple[Path, dict[str, object]]:
    """Read analytics.yaml, returning the path and data dict.

    Creates the context directory if it does not exist.
    """
    cfg: TRWConfig = get_config()
    writer = FileStateWriter()
    reader = FileStateReader()
    context_dir = trw_dir / cfg.context_dir
    writer.ensure_dir(context_dir)
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = {}
    if reader.exists(analytics_path):
        data = reader.read_yaml(analytics_path)
    return analytics_path, data


def _update_core_counters(
    data: dict[str, object],
    new_learnings_count: int,
) -> tuple[int, int]:
    """Increment sessions_tracked, total_learnings, and avg_learnings_per_session.

    Returns:
        Tuple of (sessions, total_learnings) after update.
    """
    sessions = _ac._safe_int(data, "sessions_tracked") + 1
    total_learnings = _ac._safe_int(data, "total_learnings") + new_learnings_count
    data["sessions_tracked"] = sessions
    data["total_learnings"] = total_learnings
    data["avg_learnings_per_session"] = round(total_learnings / max(sessions, 1), 2)
    return sessions, total_learnings


def update_analytics(trw_dir: Path, new_learnings_count: int) -> None:
    """Update .trw/context/analytics.yaml with reflection metrics.

    Args:
        trw_dir: Path to .trw directory.
        new_learnings_count: Number of new learnings produced.
    """
    analytics_path, data = _read_analytics(trw_dir)
    _, total_learnings = _update_core_counters(data, new_learnings_count)
    FileStateWriter().write_yaml(analytics_path, data)
    logger.debug("analytics_updated", new_learnings=new_learnings_count, total=total_learnings)


def update_analytics_sync(trw_dir: Path) -> None:
    """Increment CLAUDE.md sync counter in analytics.

    Args:
        trw_dir: Path to .trw directory.
    """
    analytics_path, data = _read_analytics(trw_dir)
    data["claude_md_syncs"] = _ac._safe_int(data, "claude_md_syncs") + 1
    FileStateWriter().write_yaml(analytics_path, data)


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

    # Core counters (shared with update_analytics)
    _update_core_counters(data, new_learnings_count)

    # FR02: Reflection tracking
    if is_reflection:
        data["reflections_completed"] = _ac._safe_int(data, "reflections_completed") + 1

    # FR02: Success rate tracking
    total_outcomes = _ac._safe_int(data, "total_outcomes") + 1
    successes = _ac._safe_int(data, "successful_outcomes")
    if is_success:
        successes += 1
    data["total_outcomes"] = total_outcomes
    data["successful_outcomes"] = successes
    data["success_rate"] = round(successes / max(total_outcomes, 1), 3)

    # FR03: Q-learning activations (scan entries for q_observations > 0)
    q_activations = 0
    high_impact = 0
    try:
        from trw_mcp.state.memory_adapter import list_active_learnings
        all_active = list_active_learnings(trw_dir)
        for entry_data in all_active:
            if int(str(entry_data.get("q_observations", 0))) > 0:
                q_activations += 1
            if float(str(entry_data.get("impact", 0.5))) >= 0.7:
                high_impact += 1
    except Exception:  # broad catch: ImportError + SQLite/adapter failures
        # Fallback: YAML scan
        entries_dir = _ac._entries_path(trw_dir)
        if entries_dir.is_dir():
            for _path, entry_data in _ac._iter_entry_files(entries_dir):
                if _ac._safe_int(entry_data, "q_observations") > 0:
                    q_activations += 1
                if _ac._safe_float(entry_data, "impact", 0.5) >= 0.7:
                    high_impact += 1
    data["q_learning_activations"] = q_activations
    data["high_impact_learnings"] = high_impact

    FileStateWriter().write_yaml(analytics_path, data)
