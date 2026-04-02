"""Propensity logging for learning selection decisions.

Records every learning selection decision with probability scores and
context features. Pre-bandit: all selections are deterministic (prob=1.0).
Infrastructure for PRD-CORE-105 (Thompson Sampling) and PRD-CORE-108 (DML).

PRD-CORE-103-FR03: Propensity Logging Schema
"""

from __future__ import annotations

__all__ = ["PropensityEntry", "log_selection", "read_propensity_entries"]

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import structlog

logger = structlog.get_logger(__name__)

_LOG_DIR = "logs"
_PROPENSITY_FILE = "propensity.jsonl"
_ROTATION_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB


class PropensityEntry(TypedDict, total=False):
    """Propensity log entry for a learning selection decision.

    All fields are optional (total=False) to allow incremental construction,
    but ``log_selection`` always populates every field before writing.
    """

    timestamp: str  # ISO 8601
    selected: str  # ID of the selected learning
    selection_probability: float  # P(selected) -- 1.0 for deterministic
    candidate_set: list[str]  # IDs of all candidates considered
    runner_up: str  # ID of the next-best candidate
    exploration: bool  # True if exploration pick (false pre-bandit)
    context_phase: str  # Current ceremony phase
    context_domain: list[str]  # Active domains at selection time
    context_agent_type: str  # Agent type (e.g., "claude-code")
    session_id: str  # Session identifier


from trw_mcp.state._helpers import rotate_jsonl as _shared_rotate_jsonl


def _rotate_jsonl(log_path: Path) -> None:
    """Rotate a JSONL file if it exceeds the size threshold.

    Delegates to the shared ``rotate_jsonl`` helper from ``_helpers.py``.
    """
    _shared_rotate_jsonl(log_path, max_bytes=_ROTATION_THRESHOLD_BYTES)


def log_selection(
    trw_dir: Path,
    *,
    selected: str,
    candidate_set: list[str] | None = None,
    runner_up: str = "",
    selection_probability: float = 1.0,
    exploration: bool = False,
    context_phase: str = "",
    context_domain: list[str] | None = None,
    context_agent_type: str = "",
    session_id: str = "",
) -> None:
    """Log a learning selection decision to propensity.jsonl.

    Fail-open: never raises. Before the bandit exists (PRD-CORE-105),
    all selections are deterministic (probability=1.0, exploration=false).

    IMPORTANT: No learning content (summary, detail) is logged --
    only IDs for cross-referencing.

    Args:
        trw_dir: Path to the ``.trw`` directory.
        selected: ID of the selected learning.
        candidate_set: IDs of all candidate learnings considered.
        runner_up: ID of the next-best candidate. Auto-populated from
            ``candidate_set`` when not provided.
        selection_probability: P(selected). Defaults to 1.0 (deterministic).
        exploration: True if this was an exploration pick. Defaults to False.
        context_phase: Current ceremony phase (e.g., "IMPLEMENT").
        context_domain: Active domains at selection time.
        context_agent_type: Agent type identifier (e.g., "claude-code").
        session_id: Session identifier for cross-referencing.
    """
    try:
        log_dir = trw_dir / _LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / _PROPENSITY_FILE

        _rotate_jsonl(log_path)

        # Resolve candidate set and auto-populate runner_up
        candidates = candidate_set or []
        if not runner_up and len(candidates) >= 2:
            # Runner-up is the second element in the candidate list,
            # unless it is the selected item -- then fall back to the first.
            if candidates[1] != selected:
                runner_up = candidates[1]
            elif candidates[0] != selected:
                runner_up = candidates[0]
            else:
                runner_up = ""
        elif len(candidates) <= 1:
            runner_up = ""

        entry: PropensityEntry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "selected": selected,
            "selection_probability": selection_probability,
            "candidate_set": candidates,
            "runner_up": runner_up,
            "exploration": exploration,
            "context_phase": context_phase,
            "context_domain": context_domain or [],
            "context_agent_type": context_agent_type,
            "session_id": session_id,
        }

        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    except Exception:  # justified: fail-open, logging must never block the calling tool
        logger.debug("propensity_log_failed", exc_info=True)


def read_propensity_entries(
    trw_dir: Path,
    max_entries: int = 500,
) -> list[PropensityEntry]:
    """Read propensity entries from the log file.

    Returns the last ``max_entries`` entries (most recent). Fail-open:
    returns empty list on any error.

    Args:
        trw_dir: Path to the ``.trw`` directory.
        max_entries: Maximum number of entries to return from the tail.

    Returns:
        List of parsed PropensityEntry dicts, newest last.
    """
    log_path = trw_dir / _LOG_DIR / _PROPENSITY_FILE
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        entries: list[PropensityEntry] = []
        for line in lines[-max_entries:]:
            if line.strip():
                entries.append(json.loads(line))
        return entries
    except Exception:  # justified: fail-open, read failure returns empty
        logger.debug("propensity_read_failed", path=str(log_path), exc_info=True)
        return []
