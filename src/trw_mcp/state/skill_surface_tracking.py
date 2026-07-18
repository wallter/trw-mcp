"""Skill surface event tracking -- JSONL telemetry for skill surfacing decisions.

PRD-QUAL-111-FR01: a skill-scoped, append-only contribution evidence log,
structurally mirroring the learning-only ``surface_tracking.py`` but kept in a
SEPARATE file (``skill_surface_tracking.jsonl``). The learning log feeds
nudge-fatigue analytics (``compute_recall_pull_rate``) and requires a
``learning_id``; overloading it would couple skill lifecycle to learning
fatigue metrics, so this is an independent surface.

Records *which skill was surfaced as a candidate*, *when*, and -- honestly --
``invoked_after_surface``: whether an observable same-session skill invocation
subsequently referenced the surfaced skill. That is an
**invoked-after-surface + recency** signal, NOT a causal skill->task-success
signal (PRD-QUAL-111-NFR05). No SKILL.md body content and no query free-text is
stored -- only the skill name and a ``query_terms_matched`` integer count.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from typing_extensions import TypedDict

from trw_mcp.state._helpers import read_jsonl_tail
from trw_mcp.state._helpers import rotate_jsonl as _rotate_jsonl

logger = structlog.get_logger(__name__)

_LOG_DIR = "logs"
_SKILL_SURFACE_FILE = "skill_surface_tracking.jsonl"

__all__ = [
    "SkillSurfaceEvent",
    "log_skill_surface_event",
    "read_skill_surface_events",
]


class _SkillSurfaceEventRequired(TypedDict):
    """Required keys of a skill-surface event (PRD-QUAL-111-FR01).

    The PRD marks ``skill_name`` and ``surfaced_at`` as REQUIRED; this
    ``total=True`` base enforces that contract under ``mypy --strict`` (PEP 655).
    No SKILL.md body content and no query free-text is ever stored -- only the
    skill name and an ISO timestamp (NFR04).
    """

    skill_name: str  # Required -- identifies the surfaced skill
    surfaced_at: str  # ISO 8601 timestamp (required)


class SkillSurfaceEvent(_SkillSurfaceEventRequired, total=False):
    """Structured skill-surface event (PRD-QUAL-111-FR01).

    Required keys (``skill_name``, ``surfaced_at``) are inherited from the
    ``total=True`` base; the keys below are optional (``total=False``). No
    SKILL.md body content and no query free-text is ever stored -- only the
    skill name, an ISO timestamp, and integer/boolean metadata (NFR04).
    """

    surface_type: str  # closed set: "discovery"
    session_id: str  # Session identifier (optional)
    query_terms_matched: int  # Count of matched query terms -- never free-text
    # Honest outcome signal (NFR05): true ONLY when an observable same-session
    # skill invocation references this surfaced skill. NOT a causal success
    # signal.
    invoked_after_surface: bool


def log_skill_surface_event(
    trw_dir: Path,
    *,
    skill_name: str,
    surface_type: str = "discovery",
    session_id: str = "",
    query_terms_matched: int | None = None,
    invoked_after_surface: bool | None = None,
) -> None:
    """Append a skill-surface event to ``skill_surface_tracking.jsonl``.

    Fail-open (PRD-QUAL-111-NFR02): never raises. Logging failures are
    swallowed after a debug log entry so surface logging can never block or
    break ``discover_meta_skills``.

    The ``.trw/logs/`` dir is created ``0700`` via ``harden_dir_mode`` (NFR04).
    """
    try:
        from trw_mcp.state._paths_permissions import harden_dir_mode

        log_dir = trw_dir / _LOG_DIR
        harden_dir_mode(trw_dir, create=True)
        harden_dir_mode(log_dir, create=True)
        log_path = log_dir / _SKILL_SURFACE_FILE

        _rotate_jsonl(log_path)

        event: SkillSurfaceEvent = {
            "skill_name": skill_name,
            "surfaced_at": datetime.now(timezone.utc).isoformat(),
            "surface_type": surface_type,
        }
        if session_id:
            event["session_id"] = session_id
        if query_terms_matched is not None:
            event["query_terms_matched"] = query_terms_matched
        if invoked_after_surface is not None:
            event["invoked_after_surface"] = invoked_after_surface

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    except Exception:  # trw:intentional fail-open-surface-logging
        logger.debug("skill_surface_event_log_failed", exc_info=True)


def read_skill_surface_events(
    trw_dir: Path,
    max_events: int = 2000,
) -> list[SkillSurfaceEvent]:
    """Read skill-surface events from the tracking JSONL file.

    Returns the most recent events (up to *max_events*). Fail-open: returns an
    empty list on any error or missing file (read_jsonl_tail skips individual
    corrupt/torn lines instead of discarding the whole tail).
    """
    log_path = trw_dir / _LOG_DIR / _SKILL_SURFACE_FILE
    return cast("list[SkillSurfaceEvent]", read_jsonl_tail(log_path, max_events))
