"""Proximal reward detection from events.jsonl patterns.

PRD-CORE-104-FR03: Detects near-term signals linking nudges to agent actions.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from typing_extensions import TypedDict

logger = structlog.get_logger(__name__)


class ProximalSignal(TypedDict):
    learning_id: str
    signal_type: str
    phase: str
    turn_offset: int


_TEST_EVENTS: set[str] = {"build_check_complete", "test_run"}


def detect_proximal_signals(
    events: list[dict[str, object]],
    max_offset: int = 2,
) -> list[ProximalSignal]:
    """Detect proximal reward signals from event patterns.

    Scans for nudge_shown events, checks subsequent events for patterns.
    """
    signals: list[ProximalSignal] = []
    for i, event in enumerate(events):
        if str(event.get("event", "")) != "nudge_shown":
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        learning_id = str(data.get("learning_id", ""))
        phase = str(data.get("phase", ""))
        if not learning_id:
            continue
        for j in range(i + 1, min(i + 1 + max_offset, len(events))):
            next_name = str(events[j].get("event", ""))
            if next_name in _TEST_EVENTS:
                signals.append(
                    ProximalSignal(
                        learning_id=learning_id,
                        signal_type="test_rerun",
                        phase=phase,
                        turn_offset=j - i,
                    )
                )
                break
    return signals


def _parse_event_line(
    line: str,
    *,
    events_path: Path,
    line_number: int,
) -> dict[str, object] | None:
    """Parse one JSONL line into an event dict, or ``None`` if unusable.

    Implementation seam behind the fail-open :func:`read_recent_events`
    Interface: a corrupt or non-object line is skipped (not raised) so it
    cannot discard valid neighbours in the recent window. Skips emit a
    structured ``proximal_reward.event_line_skipped`` event carrying only the
    path, line number, and error class — never the line contents or parsed
    payload — so corruption is observable without leaking event data.
    """
    if not line.strip():
        return None
    try:
        parsed = json.loads(line)
    except ValueError as exc:  # JSONDecodeError is a ValueError subclass
        logger.warning(
            "proximal_reward.event_line_skipped",
            path=str(events_path),
            line_number=line_number,
            error_class=type(exc).__name__,
        )
        return None
    if not isinstance(parsed, dict):
        logger.warning(
            "proximal_reward.event_line_skipped",
            path=str(events_path),
            line_number=line_number,
            error_class="NonObjectJSON",
        )
        return None
    return parsed


def read_recent_events(events_path: Path, max_events: int = 200) -> list[dict[str, object]]:
    """Read most recent events from events.jsonl. Fail-open.

    Interface contract: always returns a list and never raises. A missing file
    or a file-level read/decode failure yields ``[]``; an individual malformed
    JSONL line is skipped (see :func:`_parse_event_line`) without dropping the
    valid records in the selected recent window.
    """
    if not events_path.exists():
        return []
    try:
        raw = events_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # trw:intentional fail-open; UnicodeDecodeError is ValueError
        logger.warning(
            "proximal_reward.event_file_unreadable",
            path=str(events_path),
            error_class=type(exc).__name__,
        )
        return []
    lines = raw.strip().split("\n")
    window = lines[-max_events:]
    # Line numbers are 1-based against the post-strip line list, not the slice.
    base = len(lines) - len(window)
    events: list[dict[str, object]] = []
    for offset, line in enumerate(window):
        parsed = _parse_event_line(line, events_path=events_path, line_number=base + offset + 1)
        if parsed is not None:
            events.append(parsed)
    return events


def read_proximal_event_window(
    trw_dir: Path,
    resolved_run: Path | None,
    max_events: int = 200,
) -> list[dict[str, object]]:
    """Merge, by timestamp, the two streams a proximal scan needs.

    Ledger UF-026 (second half): ``nudge_shown`` is written ONLY to
    ``{trw_dir}/context/session-events.jsonl``
    (``_ceremony_progress_state._emit_nudge_shown_event``), while every event in
    ``_TEST_EVENTS`` is written ONLY to the pinned run's ``meta/events.jsonl``
    (``build/_registration._log_build_event``). :func:`detect_proximal_signals`
    keys on *adjacency within one list*, so scanning either stream alone can
    never observe a nudge->action pair — measured on this repo: 0 ``nudge_shown``
    rows in any run's ``meta/events.jsonl`` written since 2026-04, and 0
    ``build_check_complete`` rows in ``session-events.jsonl`` ever. The detector
    therefore returned ``[]`` in production no matter how many nudges fired, and
    bridging its output to Q-learning without this merge would have wired a
    permanently-empty signal.

    Both streams stamp an ISO-8601 ``ts``, so a lexicographic sort restores true
    interleaved order. Fail-open via :func:`read_recent_events`.
    """
    events = read_recent_events(trw_dir / "context" / "session-events.jsonl", max_events)
    if resolved_run is not None:
        events += read_recent_events(resolved_run / "meta" / "events.jsonl", max_events)
    events.sort(key=lambda event: str(event.get("ts", "")))
    return events[-max_events:]


__all__ = [
    "ProximalSignal",
    "detect_proximal_signals",
    "read_proximal_event_window",
    "read_recent_events",
]
