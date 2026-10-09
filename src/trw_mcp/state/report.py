"""Run-event and phase-timeline parsing shared by AgentWorkEvidence."""

from __future__ import annotations

from datetime import datetime

from trw_mcp.models.report import DurationInfo, EventSummary, PhaseEntry


def parse_run_events(
    events: list[dict[str, object]],
) -> tuple[EventSummary, list[PhaseEntry], DurationInfo]:
    """Parse events into summary, phase timeline, and duration.

    Args:
        events: List of event dicts from events.jsonl.

    Returns:
        Tuple of (event_summary, phase_timeline, duration_info).
    """
    by_type: dict[str, int] = {}
    phase_enters: list[dict[str, object]] = []

    for evt in events:
        event_type = str(evt.get("event", "unknown"))
        by_type[event_type] = by_type.get(event_type, 0) + 1

        if event_type == "phase_enter":
            phase_enters.append(evt)

    event_summary = EventSummary(total_count=len(events), by_type=by_type)

    # Phase timeline from phase_enter events
    phase_timeline = _build_phase_timeline(phase_enters)

    # Duration from first/last event timestamps
    duration = _compute_duration(events)

    return event_summary, phase_timeline, duration


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
        t_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
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
