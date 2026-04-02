"""Proximal reward detection from events.jsonl patterns.

PRD-CORE-104-FR03: Detects near-term signals linking nudges to agent actions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import structlog

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


def read_recent_events(
    events_path: Path, max_events: int = 200
) -> list[dict[str, object]]:
    """Read most recent events from events.jsonl. Fail-open."""
    if not events_path.exists():
        return []
    try:
        lines = events_path.read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(line) for line in lines[-max_events:] if line.strip()]
    except Exception:  # justified: fail-open for resilience
        return []


__all__ = ["ProximalSignal", "detect_proximal_signals", "read_recent_events"]
