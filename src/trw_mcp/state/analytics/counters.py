"""Analytics counters — counter updates and event pattern detection.

Module C of the analytics decomposition.  Handles analytics.yaml counter
updates (sessions, learnings, reflections, success rate, Q-activations)
and event analysis (repeated operations, success patterns, tool sequences).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import structlog

import trw_mcp.state.analytics.core as _ac
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, lock_for_rmw

logger = structlog.get_logger(__name__)


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
    counts = Counter(et for event in events if (et := _ac._get_event_type(event)))
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
        success_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    ):
        patterns.append(
            {
                "event_type": event_type,
                "summary": f"Success: {event_type} ({count}x)",
                "detail": success_details.get(event_type, ""),
                "count": str(count),
            }
        )

    cfg_sp: TRWConfig = get_config()
    return patterns[: cfg_sp.reflect_max_success_patterns]


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
        preceding = [_ac._get_event_type(events[j]) or "unknown" for j in range(start, i)]
        current_type = _ac._get_event_type(event) or "unknown"
        seq = (*preceding, current_type)
        if len(seq) >= 2:
            sequence_counts[seq] = sequence_counts.get(seq, 0) + 1

    results: list[dict[str, object]] = []
    for seq, count in sorted(
        sequence_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    ):
        if count >= min_occurrences:
            rate = f"{count}/{total_anchors}" if total_anchors else "0/0"
            results.append(
                {
                    "sequence": list(seq),
                    "count": count,
                    "success_rate": rate,
                }
            )

    return results


# ---------------------------------------------------------------------------
# Analytics counter management
# ---------------------------------------------------------------------------


def _read_analytics(trw_dir: Path) -> tuple[Path, dict[str, object]]:
    """Read analytics.yaml, returning the path and data dict.

    Creates the context directory if it does not exist.
    Migrates legacy ``sessions_count`` field to ``sessions_tracked`` on read
    (FIX-050-FR06): takes the max of both values to avoid data loss.
    """
    cfg: TRWConfig = get_config()
    writer = FileStateWriter()
    reader = FileStateReader()
    context_dir = trw_dir / cfg.context_dir
    writer.ensure_dir(context_dir)
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = reader.read_yaml(analytics_path) if reader.exists(analytics_path) else {}

    # FIX-050-FR06: Migrate legacy sessions_count -> sessions_tracked.
    # sessions_count was a dead field that was never updated (counter code
    # always wrote to sessions_tracked). On first read, we take the max of
    # both values to ensure no data loss, then remove sessions_count.
    if "sessions_count" in data:
        legacy_count = _ac._safe_int(data, "sessions_count")
        tracked = _ac._safe_int(data, "sessions_tracked")
        data["sessions_tracked"] = max(legacy_count, tracked)
        del data["sessions_count"]
        writer.write_yaml(analytics_path, data)
        logger.debug(
            "analytics_sessions_count_migrated",
            legacy=legacy_count,
            tracked=data["sessions_tracked"],
        )

    return analytics_path, data


@contextmanager
def _locked_analytics(trw_dir: Path) -> Generator[tuple[Path, dict[str, object]], None, None]:
    """Read analytics.yaml under an advisory R-M-W lock, yielding (path, data).

    The dev repo runs several MCP clients (Claude Code, Codex, opencode), each
    spawning its own stdio trw-mcp process but all sharing one ``.trw/``
    filesystem, so concurrent trw_session_start / trw_learn calls would
    otherwise race the read-modify-write and silently lose counter increments
    (last-write-wins drops a session/learning from the tally). Mirrors the
    lock_for_rmw guard already on update_learning_index. The caller's mutate +
    write_yaml run inside the ``with`` while the lock is held; write_yaml itself
    is an atomic rename, so the combination is a serialized, crash-safe RMW.
    """
    cfg: TRWConfig = get_config()
    context_dir = trw_dir / cfg.context_dir
    FileStateWriter().ensure_dir(context_dir)
    analytics_path = context_dir / "analytics.yaml"
    with lock_for_rmw(analytics_path):
        _, data = _read_analytics(trw_dir)
        yield analytics_path, data


def increment_session_start_counter(trw_dir: Path) -> None:
    """Increment sessions_tracked when a session starts (FIX-050-FR06).

    Called from trw_session_start after the session_start event is logged.
    This ensures sessions_tracked reflects actual session starts, not just
    completed deliveries (which is when update_analytics was previously called).

    Args:
        trw_dir: Path to .trw directory.
    """
    with _locked_analytics(trw_dir) as (analytics_path, data):
        tracked = _ac._safe_int(data, "sessions_tracked") + 1
        data["sessions_tracked"] = tracked
        FileStateWriter().write_yaml(analytics_path, data)
    logger.debug("session_start_counter_incremented", sessions_tracked=tracked)


def _update_core_counters(
    data: dict[str, object],
    new_learnings_count: int,
) -> tuple[int, int]:
    """Increment sessions_tracked, total_learnings, and avg_learnings_per_session.

    Returns:
        Tuple of (sessions, total_learnings) after update.
    """
    sessions = _ac._safe_int(data, "sessions_delivered") + 1
    total_learnings = _ac._safe_int(data, "total_learnings") + new_learnings_count
    data["sessions_delivered"] = sessions
    data["total_learnings"] = total_learnings
    data["avg_learnings_per_session"] = round(total_learnings / max(sessions, 1), 2)
    return sessions, total_learnings


def update_analytics(trw_dir: Path, new_learnings_count: int) -> None:
    """Update .trw/context/analytics.yaml with reflection metrics.

    Args:
        trw_dir: Path to .trw directory.
        new_learnings_count: Number of new learnings produced.
    """
    with _locked_analytics(trw_dir) as (analytics_path, data):
        _, total_learnings = _update_core_counters(data, new_learnings_count)
        FileStateWriter().write_yaml(analytics_path, data)
    logger.debug("analytics_updated", new_learnings=new_learnings_count, total=total_learnings)


def update_analytics_sync(trw_dir: Path) -> None:
    """Increment CLAUDE.md sync counter in analytics.

    Args:
        trw_dir: Path to .trw directory.
    """
    with _locked_analytics(trw_dir) as (analytics_path, data):
        data["claude_md_syncs"] = _ac._safe_int(data, "claude_md_syncs") + 1
        FileStateWriter().write_yaml(analytics_path, data)
