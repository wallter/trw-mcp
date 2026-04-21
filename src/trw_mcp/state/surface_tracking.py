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
    # PRD-CORE-103: Metadata fields for stratified analysis
    client_profile: str  # Client profile identifier (e.g., "claude-code")
    model_family: str  # Model family (e.g., "claude", "gpt")
    trw_version: str  # Framework version (e.g., "v24.4_TRW")


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
    client_profile: str = "",
    model_family: str = "",
    trw_version: str = "",
) -> None:
    """Append a surface event to ``surface_tracking.jsonl``.

    Fail-open: never raises.  Logging failures are silently swallowed
    after a debug log entry.

    IMPORTANT: No learning content (summary, detail) is logged --
    only the ``learning_id`` for cross-referencing.

    PRD-CORE-103: When *client_profile*, *model_family*, or *trw_version*
    are empty strings, auto-detection from config is attempted (best-effort).
    """
    try:
        log_dir = trw_dir / _LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / _SURFACE_FILE

        # Rotate if needed
        _rotate_jsonl(log_path)

        # PRD-CORE-103: Auto-detect metadata fields from config when empty
        if not client_profile or not trw_version:
            try:
                from trw_mcp.models.config import get_config

                cfg = get_config()
                if not client_profile:
                    client_profile = (
                        cfg.client_profile.client_id
                        if hasattr(cfg.client_profile, "client_id")
                        else str(cfg.client_profile)
                    )
                if not trw_version:
                    trw_version = cfg.framework_version or ""
            except Exception:  # noqa: S110  # justified: fail-open, auto-detection is best-effort
                pass

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
            "client_profile": client_profile,
            "model_family": model_family,
            "trw_version": trw_version,
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


def compute_recall_pull_rate(
    trw_dir: Path,
    *,
    session_id: str | None = None,
) -> tuple[float, int, list[str]]:
    """Compute the fraction of nudged learnings that led to a trw_recall.

    PRD-CORE-144 FR02/FR04:
    - When *session_id* is provided and non-empty, reads ALL events from
      the log and filters to events matching that session. This fixes the
      bug where the last-500-line tail missed every nudge for large logs.
    - When *session_id* is None or empty, preserves legacy last-500 behavior
      so callers that don't yet pass a session id are unaffected.
    - Return tuple now includes the list of unique learning IDs observed
      in the scoped event set (first-seen order preserved), needed by
      FR04 session_metrics.learning_exposure.ids.

    Returns:
        Tuple of (pull_rate, nudge_count, learning_ids).
        ``(0.0, 0, [])`` when no nudge events found.
    """
    if session_id:
        events = _read_all_surface_events_for_session(trw_dir, session_id)
    else:
        events = read_surface_events(trw_dir)

    if not events:
        return 0.0, 0, []

    nudge_ids: set[str] = set()
    recall_ids: set[str] = set()
    ordered_ids: list[str] = []
    seen: set[str] = set()

    for event in events:
        surface_type = event.get("surface_type", "")
        learning_id = event.get("learning_id", "")
        if not learning_id:
            continue
        if learning_id not in seen:
            seen.add(learning_id)
            ordered_ids.append(learning_id)
        if surface_type == "nudge":
            nudge_ids.add(learning_id)
        elif surface_type == "recall":
            recall_ids.add(learning_id)

    if not nudge_ids:
        return 0.0, 0, ordered_ids

    pulled = nudge_ids & recall_ids
    return len(pulled) / len(nudge_ids), len(nudge_ids), ordered_ids


def _read_all_surface_events_for_session(
    trw_dir: Path,
    session_id: str,
    *,
    hard_cap_lines: int = 100_000,
) -> list[SurfaceEvent]:
    """Scan the full surface log (up to a hard cap) and filter by session.

    PRD-CORE-144 RISK-001: cap at 100K lines to bound IO for pathological
    log growth. Rotation (_rotate_jsonl) runs on every append, so in
    practice files stay well under this limit.
    """
    log_path = trw_dir / _LOG_DIR / _SURFACE_FILE
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").split("\n")
        if len(lines) > hard_cap_lines:
            lines = lines[-hard_cap_lines:]
        matched: list[SurfaceEvent] = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            try:
                ev: SurfaceEvent = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                continue
            if ev.get("session_id") == session_id:
                matched.append(ev)
        return matched
    except Exception:  # justified: fail-open, read failure returns empty
        return []


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
        pull_rate, nudge_count, _ = compute_recall_pull_rate(trw_dir)

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
