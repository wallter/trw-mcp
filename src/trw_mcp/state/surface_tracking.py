"""Surface event tracking -- JSONL telemetry for learning surfacing decisions.

Records *when* and *why* a learning was surfaced (nudge, recall, session_start,
phase_transition) without storing any learning content. Only the learning_id
is logged for cross-referencing with the learning store.

Supports PRD-CORE-115 surface telemetry and fatigue detection (Task 6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_LOG_DIR = "logs"
_SURFACE_FILE = "surface_tracking.jsonl"

__all__ = [
    "NudgeFatigueResult",
    "SurfaceEvent",
    "check_nudge_fatigue",
    "compute_recall_pull_rate",
    "log_surface_event",
    "read_surface_events",
]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

from typing import TypedDict  # noqa: E402


class SurfaceEvent(TypedDict, total=False):
    """Structured surface event for learning surfacing telemetry.

    All fields except ``learning_id`` and ``surfaced_at`` are optional
    (total=False).  No learning content (summary, detail) is stored --
    only the ``learning_id`` for cross-referencing.
    """

    learning_id: str  # Required -- identifies the surfaced learning
    surfaced_at: str  # ISO 8601 timestamp (required)
    surface_type: str  # "nudge" | "session_start" | "recall" | "phase_transition"
    phase: str  # Current ceremony phase
    domain_match: list[str]  # Inferred domains from file context
    files_context: list[str]  # File paths that informed the surface decision
    prd_boosted: bool  # Whether boosted by PRD knowledge linkage
    bandit_score: float  # Selection score (0.0 before bandit)
    exploration: bool  # Exploration pick (false before bandit)
    session_id: str  # Session identifier


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


from trw_mcp.state._helpers import rotate_jsonl as _rotate_jsonl  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log_surface_event(
    trw_dir: Path,
    *,
    learning_id: str,
    surface_type: str,
    phase: str = "",
    domain_match: list[str] | None = None,
    files_context: list[str] | None = None,
    prd_boosted: bool = False,
    bandit_score: float = 0.0,
    exploration: bool = False,
    session_id: str = "",
) -> None:
    """Append a surface event to ``surface_tracking.jsonl``.

    Fail-open: never raises.  Logging failures are silently swallowed
    after a debug log entry.

    IMPORTANT: No learning content (summary, detail) is logged --
    only the ``learning_id`` for cross-referencing.
    """
    try:
        log_dir = trw_dir / _LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / _SURFACE_FILE

        # Rotate if needed
        _rotate_jsonl(log_path)

        event: SurfaceEvent = {
            "learning_id": learning_id,
            "surfaced_at": datetime.now(timezone.utc).isoformat(),
            "surface_type": surface_type,
            "phase": phase or "",
            "domain_match": domain_match or [],
            "files_context": files_context or [],
            "prd_boosted": prd_boosted,
            "bandit_score": bandit_score,
            "exploration": exploration,
            "session_id": session_id,
        }

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    except Exception:  # justified: fail-open, surface logging must not block callers
        logger.debug("surface_event_log_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Reading (for fatigue detection -- Task 6)
# ---------------------------------------------------------------------------


def read_surface_events(trw_dir: Path, max_events: int = 500) -> list[SurfaceEvent]:
    """Read surface events from the tracking JSONL file.

    Returns the most recent events (up to *max_events*).  Fail-open:
    returns an empty list on any error.
    """
    log_path = trw_dir / _LOG_DIR / _SURFACE_FILE
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        events: list[SurfaceEvent] = [json.loads(line) for line in lines[-max_events:] if line.strip()]
        return events
    except Exception:  # justified: fail-open, read failure returns empty
        return []


# ---------------------------------------------------------------------------
# Fatigue detection (PRD-CORE-103-FR05)
# ---------------------------------------------------------------------------


def compute_recall_pull_rate(trw_dir: Path) -> tuple[float, int]:
    """Compute the fraction of nudged learnings that led to a trw_recall.

    Scans surface_tracking.jsonl for nudge events, then checks if the
    same learning_id appears in a subsequent recall event within the
    same session.

    Returns:
        Tuple of (pull_rate: float 0.0-1.0, nudge_count: int).
        Returns (0.0, 0) if no nudge events found.
    """
    events = read_surface_events(trw_dir)
    if not events:
        return 0.0, 0

    nudge_ids: set[str] = set()
    recall_ids: set[str] = set()

    for event in events:
        surface_type = event.get("surface_type", "")
        learning_id = event.get("learning_id", "")
        if not learning_id:
            continue
        if surface_type == "nudge":
            nudge_ids.add(learning_id)
        elif surface_type == "recall":
            recall_ids.add(learning_id)

    if not nudge_ids:
        return 0.0, 0

    pulled = nudge_ids & recall_ids
    return len(pulled) / len(nudge_ids), len(nudge_ids)


class NudgeFatigueResult(TypedDict):
    """Typed result from check_nudge_fatigue."""

    recall_pull_rate: float
    nudge_count: int
    nudge_fatigue_warning: bool
    sessions_analyzed: int


def check_nudge_fatigue(
    trw_dir: Path,
    *,
    threshold: float = 0.10,
    min_sessions: int = 5,
) -> NudgeFatigueResult:
    """Check for nudge fatigue across recent sessions.

    Returns a dict with:
    - recall_pull_rate: float (0.0-1.0)
    - nudge_count: int
    - nudge_fatigue_warning: bool (True if rate < threshold for min_sessions)
    - sessions_analyzed: int

    Fail-open: returns neutral results on any error.
    """
    try:
        pull_rate, nudge_count = compute_recall_pull_rate(trw_dir)

        # Simple heuristic: if we have enough nudges and pull rate is low, warn
        # A more sophisticated version would track across sessions
        warning = nudge_count >= min_sessions and pull_rate < threshold

        return {
            "recall_pull_rate": round(pull_rate, 4),
            "nudge_count": nudge_count,
            "nudge_fatigue_warning": warning,
            "sessions_analyzed": 1,  # Single-session for now
        }
    except Exception:  # justified: fail-open, fatigue detection must not block callers
        return {
            "recall_pull_rate": 0.0,
            "nudge_count": 0,
            "nudge_fatigue_warning": False,
            "sessions_analyzed": 0,
        }
