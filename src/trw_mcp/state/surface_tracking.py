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

from typing import TypedDict, cast  # noqa: E402


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
    # Live nudge timing-validity (nudge-deep-dive work target #4). Stamped at
    # emission so mistimed nudges (fired AFTER the step was already done) are
    # detectable live, without the post-hoc step_timestamps the eval pipeline
    # needs. Only present on surface_type=="nudge" events.
    nudge_step: str  # Ceremony step the nudge targeted (e.g., "checkpoint")
    is_timely: bool  # True if the targeted step was still pending at emission
    step_distance_from_call: int  # completed-step index minus targeted-step index
    # Live A/B (work target #6): the experiment arm label + the messenger that
    # produced this nudge, so population comparison can slice REAL traffic by
    # arm. Only present on surface_type=="nudge" events.
    nudge_variant: str  # Operator-set A/B arm label (config.nudge_variant)
    messenger: str  # Active messenger that produced the nudge (e.g., "minimal")


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


from trw_mcp.state._helpers import read_jsonl_tail  # noqa: E402
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
    nudge_step: str = "",
    is_timely: bool | None = None,
    step_distance_from_call: int | None = None,
    nudge_variant: str = "",
    messenger: str = "",
) -> None:
    """Append a surface event to ``surface_tracking.jsonl``.

    Fail-open: never raises.  Logging failures are silently swallowed
    after a debug log entry.

    IMPORTANT: No learning content (summary, detail) is logged --
    only the ``learning_id`` for cross-referencing.

    PRD-CORE-103: When *client_profile*, *model_family*, or *trw_version*
    are empty strings, auto-detection from config is attempted (best-effort).

    Nudge-deep-dive work target #4: ``nudge_step`` / ``is_timely`` /
    ``step_distance_from_call`` capture live timing validity for nudge
    surfaces. They are only written when supplied (``is_timely`` /
    ``step_distance_from_call`` non-None, ``nudge_step`` non-empty), so
    non-nudge surface events keep their original shape.

    Nudge-deep-dive work target #6: ``nudge_variant`` (the A/B arm label from
    ``config.nudge_variant``) and ``messenger`` (the messenger that produced the
    nudge) are stamped on nudge events so population comparison can slice live
    traffic by arm. Only written when non-empty.
    """
    try:
        from trw_mcp.state._paths_permissions import harden_dir_mode

        log_dir = trw_dir / _LOG_DIR
        # PRD-QUAL-110-FR02 follow-up: .trw/logs/ holds session state; create
        # 0700 (also tightens .trw root if first-touched here).
        harden_dir_mode(trw_dir, create=True)
        harden_dir_mode(log_dir, create=True)
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
            except Exception:  # justified: fail-open, auto-detection is best-effort
                logger.debug("surface_event_config_probe_failed", exc_info=True)

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

        # Work target #4: stamp live timing-validity only when provided, so
        # non-nudge surfaces (recall/session_start/phase_transition) are byte
        # -for-byte unchanged and the eval-side schema contract is untouched.
        if nudge_step:
            event["nudge_step"] = nudge_step
        if is_timely is not None:
            event["is_timely"] = is_timely
        if step_distance_from_call is not None:
            event["step_distance_from_call"] = step_distance_from_call
        if nudge_variant:
            event["nudge_variant"] = nudge_variant
        if messenger:
            event["messenger"] = messenger

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
    # read_jsonl_tail skips individual corrupt lines (e.g. a torn concurrent
    # append) instead of discarding the whole tail, matching the per-line
    # recovery used by _read_all_surface_events_for_session over the same log.
    return cast("list[SurfaceEvent]", read_jsonl_tail(log_path, max_events))


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
        raw = log_path.read_bytes()
    except OSError:
        return []
    # Split on the newline byte and decode each line individually, mirroring
    # read_jsonl_tail over the same log: a single non-UTF-8 byte row (a torn
    # append) is dropped on its own UnicodeDecodeError rather than failing a
    # whole-file decode and discarding every valid event for the session.
    byte_lines = raw.split(b"\n")
    if len(byte_lines) > hard_cap_lines:
        byte_lines = byte_lines[-hard_cap_lines:]
    matched: list[SurfaceEvent] = []
    for byte_line in byte_lines:
        s = byte_line.strip()
        if not s:
            continue
        try:
            parsed = json.loads(s.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        # Non-object lines (bare scalars/lists) have no .get; dropping them
        # here keeps one stray line from raising into the caller. Mirrors
        # read_jsonl_tail's isinstance(dict) guard.
        if not isinstance(parsed, dict):
            continue
        ev = cast("SurfaceEvent", parsed)
        if ev.get("session_id") == session_id:
            matched.append(ev)
    return matched


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
