"""Meta-tune correlation engine — join push events to outcomes by key.

Joins PUSH events (push_write, push_ephemeral, pull_tool_call) to
OUTCOME events (edit_correlated, or other edit/tool events) by
JOIN_KEY_FIELDS (session_id, file_path) within a rolling time window.

Consumes CLIENT_CORRECTION_FACTORS and DEFAULT_CORRELATION_WINDOW_SECONDS
from _manifest_models.py (previously defined but unused).

PRD-DIST-2400 §meta-tune.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.channels._manifest_models import (
    CLIENT_CORRECTION_FACTORS,
    DEFAULT_CORRELATION_WINDOW_SECONDS,
)

log = structlog.get_logger(__name__)

__all__ = [
    "CorrelationEvent",
    "CorrelationResult",
    "adjusted_rate",
    "correlate",
    "load_events",
]

# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------

PUSH_EVENT_TYPES: frozenset[str] = frozenset(
    {"push_write", "push_ephemeral", "pull_tool_call"}
)

OUTCOME_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "edit_correlated",
        "subagent_outcome",
        "snapshot_written",
    }
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CorrelationEvent(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str
    channel_id: str
    client: str
    ts: str
    event_type: str
    session_id: str | None = None
    file_path: str | None = None


class CorrelationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    channel_id: str
    client: str
    total_pushes: int
    correlated: int
    raw_rate: float
    adj_rate: float


# ---------------------------------------------------------------------------
# I/O helper
# ---------------------------------------------------------------------------


def load_events(log_path: Path) -> list[dict[str, Any]]:
    """Read and parse channel-events.jsonl; skip malformed lines fail-open."""
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.debug(
            "meta_tune_load_events_read_error",
            path=str(log_path),
            error=str(exc),
            outcome="load_failed",
        )
        return []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            log.debug(
                "meta_tune_load_events_parse_error",
                path=str(log_path),
                line=lineno,
                error=str(exc),
                outcome="line_skipped",
            )
            continue
        if not isinstance(obj, dict):
            continue
        events.append(obj)
    return events


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def _ts_to_seconds(ts_str: str) -> float | None:
    """Parse ISO-8601 UTC ts to epoch seconds.  Returns None on failure."""
    import re
    from datetime import datetime, timezone

    # Strip fractional seconds + Z
    clean = re.sub(r"\.\d+Z$", "Z", ts_str).rstrip("Z")
    fmts = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
    for fmt in fmts:
        try:
            dt = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Core correlator
# ---------------------------------------------------------------------------


def correlate(
    events: list[dict[str, Any]],
    *,
    window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS,
) -> list[CorrelationResult]:
    """Correlate push events to outcomes within *window_seconds*.

    Algorithm:
    1. Separate events into pushes and outcomes.
    2. For each push, check if any outcome shares the same
       (session_id, file_path) key and falls within the time window.
    3. Aggregate per (channel_id, client): total_pushes + correlated count.
    4. Compute raw_rate = correlated / total_pushes.
    5. Apply correction factor via adjusted_rate().
    """
    # Classify events
    pushes: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []

    for ev in events:
        et = ev.get("event_type", "")
        if et in PUSH_EVENT_TYPES:
            pushes.append(ev)
        elif et in OUTCOME_EVENT_TYPES:
            outcomes.append(ev)

    # Parse outcome timestamps once
    parsed_outcomes: list[tuple[str | None, str | None, float]] = []
    for oc in outcomes:
        t = _ts_to_seconds(oc.get("ts", ""))
        if t is None:
            continue
        parsed_outcomes.append(
            (oc.get("session_id"), oc.get("file_path"), t)
        )

    # Aggregate per (channel_id, client)
    totals: dict[tuple[str, str], int] = {}
    correlated: dict[tuple[str, str], int] = {}

    for push in pushes:
        channel_id = push.get("channel_id", "")
        client = push.get("client", "")
        key = (channel_id, client)
        totals[key] = totals.get(key, 0) + 1

        push_ts = _ts_to_seconds(push.get("ts", ""))
        if push_ts is None:
            continue

        p_session = push.get("session_id")
        p_file = push.get("file_path")

        # A push correlates if any outcome shares the join key within window
        matched = False
        for oc_session, oc_file, oc_ts in parsed_outcomes:
            if oc_session != p_session or oc_file != p_file:
                continue
            delta = oc_ts - push_ts
            if 0 <= delta <= window_seconds:
                matched = True
                break
        if matched:
            correlated[key] = correlated.get(key, 0) + 1

    results: list[CorrelationResult] = []
    for key, total in totals.items():
        channel_id, client = key
        corr = correlated.get(key, 0)
        raw = corr / total if total > 0 else 0.0
        adj = adjusted_rate(raw, client)
        results.append(
            CorrelationResult(
                channel_id=channel_id,
                client=client,
                total_pushes=total,
                correlated=corr,
                raw_rate=raw,
                adj_rate=adj,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Correction factor
# ---------------------------------------------------------------------------


def adjusted_rate(raw_rate: float, client: str) -> float:
    """Apply CLIENT_CORRECTION_FACTORS: min(raw / factor, 1.0).

    Unknown clients default to no adjustment (factor=1.0).
    """
    factor = CLIENT_CORRECTION_FACTORS.get(client, 1.0)
    if factor <= 0:
        return 0.0
    return min(raw_rate / factor, 1.0)
