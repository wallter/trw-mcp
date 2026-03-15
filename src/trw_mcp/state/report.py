"""Post-run analytics — event parsing, phase timeline, learning yield.

PRD-CORE-030-FR02/FR03: Pure functions that read run artifacts and compute
structured metrics for the RunReport model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.report import (
    BuildSummary,
    DurationInfo,
    EventSummary,
    LearningSummary,
    PhaseEntry,
    RunReport,
)
from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
from trw_mcp.state._helpers import safe_float
from trw_mcp.state.memory_adapter import list_active_learnings
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()

HIGH_IMPACT_THRESHOLD = 0.7


def parse_run_events(
    events: list[dict[str, object]],
) -> tuple[EventSummary, list[PhaseEntry], DurationInfo, float]:
    """Parse events into summary, phase timeline, duration, and reversion rate.

    Args:
        events: List of event dicts from events.jsonl.

    Returns:
        Tuple of (event_summary, phase_timeline, duration_info, reversion_rate).
    """
    by_type: dict[str, int] = {}
    phase_enters: list[dict[str, object]] = []
    revert_count = 0

    for evt in events:
        event_type = str(evt.get("event", "unknown"))
        by_type[event_type] = by_type.get(event_type, 0) + 1

        if event_type == "phase_enter":
            phase_enters.append(evt)
        elif event_type == "phase_revert":
            revert_count += 1

    event_summary = EventSummary(total_count=len(events), by_type=by_type)

    # Phase timeline from phase_enter events
    phase_timeline = _build_phase_timeline(phase_enters)

    # Duration from first/last event timestamps
    duration = _compute_duration(events)

    # Reversion rate
    total_transitions = len(phase_enters) + revert_count
    reversion_rate = revert_count / total_transitions if total_transitions > 0 else 0.0

    return event_summary, phase_timeline, duration, reversion_rate


def _build_phase_timeline(
    phase_enters: list[dict[str, object]],
) -> list[PhaseEntry]:
    """Build phase timeline from ordered phase_enter events.

    Each phase starts at its phase_enter timestamp and ends when the next
    phase_enter occurs (or remains open for the last phase).
    """
    timeline: list[PhaseEntry] = []

    for i, evt in enumerate(phase_enters):
        phase = str(evt.get("phase", evt.get("to_phase", "unknown")))
        entered_at = str(evt.get("ts", ""))

        exited_at: str | None = None
        duration_seconds: float | None = None

        if i + 1 < len(phase_enters):
            exited_at = str(phase_enters[i + 1].get("ts", ""))
            duration_seconds = _ts_diff_seconds(entered_at, exited_at)

        timeline.append(
            PhaseEntry(
                phase=phase,
                entered_at=entered_at,
                exited_at=exited_at,
                duration_seconds=duration_seconds,
            )
        )

    return timeline


def _ts_diff_seconds(start: str, end: str) -> float | None:
    """Compute seconds between two ISO 8601 timestamps.

    Returns None if either timestamp cannot be parsed.
    """
    try:
        t_start = datetime.fromisoformat(start)
        t_end = datetime.fromisoformat(end)
        return (t_end - t_start).total_seconds()
    except (ValueError, TypeError):
        return None


def _compute_duration(events: list[dict[str, object]]) -> DurationInfo:
    """Compute run duration from first and last event timestamps."""
    if not events:
        return DurationInfo()

    first_ts = str(events[0].get("ts", ""))
    last_ts = str(events[-1].get("ts", ""))
    elapsed = _ts_diff_seconds(first_ts, last_ts)

    return DurationInfo(
        start_ts=first_ts or None,
        end_ts=last_ts or None,
        elapsed_seconds=elapsed,
    )


def compute_learning_yield(
    trw_dir: Path,
    reader: FileStateReader,
    run_start: str | None = None,
    run_end: str | None = None,
) -> LearningSummary:
    """Compute learning yield from SQLite via memory_adapter.

    Queries active learnings and filters by date range when
    run_start/run_end are provided.

    Args:
        trw_dir: Path to the .trw directory.
        reader: File state reader (unused, kept for API compatibility).
        run_start: ISO timestamp of run start (for date filtering).
        run_end: ISO timestamp of run end (for date filtering).

    Returns:
        LearningSummary with counts, average impact, and tags.
    """
    start_date = _parse_date(run_start)
    end_date = _parse_date(run_end)

    try:
        entries = list_active_learnings(trw_dir, min_impact=0.0, limit=DEFAULT_LIST_LIMIT)
    except Exception:  # justified: fail-open, learning yield is optional report enrichment
        logger.warning("learning_yield_sqlite_failed", trw_dir=str(trw_dir))
        return LearningSummary()

    impacts: list[float] = []
    tags_set: set[str] = set()
    high_count = 0

    for data in entries:
        # Date filter: match entries created within the run window
        if start_date and end_date:
            created = str(data.get("created", ""))
            if not _date_in_range(created, start_date, end_date):
                continue

        impact = safe_float(data, "impact", 0.5)
        impacts.append(impact)
        if impact >= HIGH_IMPACT_THRESHOLD:
            high_count += 1

        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, list):
            tags_set.update(str(t) for t in raw_tags)

    avg_impact = sum(impacts) / len(impacts) if impacts else 0.0

    return LearningSummary(
        total_produced=len(impacts),
        avg_impact=round(avg_impact, 3),
        high_impact_count=high_count,
        tags_used=sorted(tags_set),
    )


def _parse_date(ts: str | None) -> str | None:
    """Extract YYYY-MM-DD from an ISO timestamp, or return None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _date_in_range(created: str, start_date: str, end_date: str) -> bool:
    """Check if a date string falls within [start_date, end_date]."""
    if not created:
        return False
    # Handle date-only strings (YYYY-MM-DD) and full ISO timestamps
    created_date = created[:10]
    return start_date <= created_date <= end_date


def assemble_report(
    run_path: Path,
    reader: FileStateReader,
    trw_dir: Path,
) -> RunReport:
    """Assemble a complete RunReport from run directory artifacts.

    PRD-CORE-030-FR04/FR05: Reads run.yaml (required), events.jsonl,
    checkpoints.jsonl, build-status.yaml (all optional with graceful fallback).

    Args:
        run_path: Path to the run directory.
        reader: File state reader.
        trw_dir: Path to the .trw directory.

    Returns:
        Fully assembled RunReport.

    Raises:
        StateError: If run.yaml cannot be read.
    """
    meta_path = run_path / "meta"

    # Required: run.yaml
    state_data = reader.read_yaml(meta_path / "run.yaml")

    # Optional: events.jsonl
    events_path = meta_path / "events.jsonl"
    events = reader.read_jsonl(events_path) if events_path.exists() else []

    # Parse events
    event_summary, phase_timeline, duration, reversion_rate = parse_run_events(events)

    # Optional: checkpoints.jsonl
    checkpoints_path = meta_path / "checkpoints.jsonl"
    checkpoint_count = 0
    if checkpoints_path.exists():
        checkpoints = reader.read_jsonl(checkpoints_path)
        checkpoint_count = len(checkpoints)

    # Optional: build-status.yaml
    build: BuildSummary | None = None
    build_path = trw_dir / "context" / "build-status.yaml"
    if build_path.exists():
        try:
            build_data = reader.read_yaml(build_path)
            build = BuildSummary(
                tests_passed=bool(build_data.get("tests_passed", False)),
                mypy_clean=bool(build_data.get("mypy_clean", False)),
                coverage_pct=safe_float(build_data, "coverage_pct", 0.0),
                test_count=int(str(build_data.get("test_count", 0))),
                duration_secs=safe_float(build_data, "duration_secs", 0.0),
            )
        except Exception:  # justified: fail-open, build status is optional report section
            logger.warning("build_status_read_failed", path=str(build_path))

    # Optional: learning yield
    learning_summary = compute_learning_yield(
        trw_dir,
        reader,
        run_start=duration.start_ts,
        run_end=duration.end_ts,
    )

    generated_at = datetime.now(timezone.utc).isoformat()

    prd_scope_raw = state_data.get("prd_scope", [])
    prd_scope = list(prd_scope_raw) if isinstance(prd_scope_raw, list) else []

    return RunReport(
        run_id=str(state_data.get("run_id", "unknown")),
        task=str(state_data.get("task", "unknown")),
        status=str(state_data.get("status", "unknown")),
        phase=str(state_data.get("phase", "unknown")),
        framework=str(state_data.get("framework", "")),
        run_type=str(state_data.get("run_type", "implementation")),
        generated_at=generated_at,
        prd_scope=[str(p) for p in prd_scope],
        duration=duration,
        phase_timeline=phase_timeline,
        event_summary=event_summary,
        checkpoint_count=checkpoint_count,
        learning_summary=learning_summary,
        build=build,
        reversion_rate=round(reversion_rate, 4),
    )
