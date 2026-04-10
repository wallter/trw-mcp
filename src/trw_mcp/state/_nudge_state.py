"""Nudge state management — persistence, read/write, and mutation helpers.

Extracted from ceremony_nudge.py (PRD-CORE-074 FR04).

Bounded context: state I/O and mutation. No decision logic, no message text.

Design constraints:
- All reads are fail-open: missing or corrupted file returns defaults, never raises.
- Writes are atomic: write to temp file then os.rename (POSIX atomic on same filesystem).
- JSON format (not YAML) for fast parsing.
- No external dependencies beyond stdlib + dataclasses.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Shared constants (used by both _nudge_messages and _nudge_rules)
# ---------------------------------------------------------------------------

# Step names in display order (FR01 PRD-CORE-084: includes review)
_STEPS: tuple[str, ...] = ("session_start", "checkpoint", "build_check", "review", "deliver")

# ---------------------------------------------------------------------------
# Nudge history entry (PRD-CORE-103-FR02)
# ---------------------------------------------------------------------------


class NudgeHistoryEntry(TypedDict):
    """Record of when/where a learning was shown as a nudge."""

    phases_shown: list[str]  # Phases where this learning was shown
    turn_first_shown: int  # Tool response number when first shown
    last_shown_turn: int  # Tool response number when last shown


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


@dataclass
class CeremonyState:
    """Tracks ceremony step completion for the current session."""

    session_started: bool = False
    checkpoint_count: int = 0
    last_checkpoint_ts: str | None = None  # ISO timestamp
    files_modified_since_checkpoint: int = 0
    build_check_result: str | None = None  # "passed" | "failed" | None
    deliver_called: bool = False
    learnings_this_session: int = 0
    nudge_counts: dict[str, int] = field(default_factory=dict)  # step -> nudge count
    phase: str = "early"  # early, implement, validate, review, deliver, done
    # PRD-CORE-105 P0: Track previous phase for transition detection
    previous_phase: str = ""
    # FR01 (PRD-CORE-084): Review tracking fields
    review_called: bool = False
    review_verdict: str | None = None  # "pass" | "warn" | "block" | None
    review_p0_count: int = 0
    # PRD-CORE-103-FR02: Nudge history for deduplication
    nudge_history: dict[str, NudgeHistoryEntry] = field(default_factory=dict)
    # PRD-CORE-129: Pool-based nudge tracking
    pool_nudge_counts: dict[str, int] = field(default_factory=dict)
    pool_ignore_counts: dict[str, int] = field(default_factory=dict)
    pool_cooldown_until: dict[str, int] = field(default_factory=dict)
    tool_call_counter: int = 0
    last_nudge_pool: str = ""


# ---------------------------------------------------------------------------
# NudgeContext dataclass (FR02, PRD-CORE-084)
# ---------------------------------------------------------------------------


@dataclass
class NudgeContext:
    """Contextual information from the tool call that triggered the nudge."""

    tool_name: str = ""
    tool_success: bool = True
    build_passed: bool | None = None
    review_verdict: str | None = None
    review_p0_count: int = 0
    is_subagent: bool = False


# ---------------------------------------------------------------------------
# Tool name constants
# ---------------------------------------------------------------------------


class ToolName:
    """Constants for NudgeContext tool_name values."""

    BUILD_CHECK = "build_check"
    REVIEW = "review"
    CHECKPOINT = "checkpoint"
    LEARN = "learn"
    SESSION_START = "session_start"
    DELIVER = "deliver"
    INIT = "init"
    RECALL = "recall"
    STATUS = "status"
    PRD_CREATE = "prd_create"
    PRD_VALIDATE = "prd_validate"


# ---------------------------------------------------------------------------
# File path helper
# ---------------------------------------------------------------------------


def _state_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-state.json"


# ---------------------------------------------------------------------------
# Internal deserialization helper
# ---------------------------------------------------------------------------


def _parse_nudge_history(raw: object) -> dict[str, NudgeHistoryEntry]:
    """Parse nudge_history from JSON with fail-open defaults."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, NudgeHistoryEntry] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        try:
            result[str(key)] = NudgeHistoryEntry(
                phases_shown=[str(p) for p in val.get("phases_shown", []) if isinstance(p, str)],
                turn_first_shown=int(val.get("turn_first_shown", 0)),
                last_shown_turn=int(val.get("last_shown_turn", 0)),
            )
        except (TypeError, ValueError):
            continue  # skip malformed entries
    return result


def _from_dict(data: dict[str, object]) -> CeremonyState:
    """Deserialize a CeremonyState from a plain dict.

    Unknown or malformed fields are silently ignored (fail-open).
    """
    nudge_raw = data.get("nudge_counts", {})
    nudge_counts: dict[str, int] = (
        {k: v for k, v in nudge_raw.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(nudge_raw, dict)
        else {}
    )

    def _bool(key: str, default: bool = False) -> bool:
        val = data.get(key, default)
        return bool(val) if isinstance(val, bool) else default

    def _int(key: str, default: int = 0) -> int:
        val = data.get(key, default)
        return int(val) if isinstance(val, (int, float)) else default

    def _opt_str(key: str) -> str | None:
        val = data.get(key)
        return str(val) if isinstance(val, str) else None

    def _str(key: str, default: str = "") -> str:
        val = data.get(key, default)
        return str(val) if isinstance(val, str) else default

    def _dict_str_int(key: str) -> dict[str, int]:
        raw = data.get(key, {})
        if not isinstance(raw, dict):
            return {}
        return {k: int(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, (int, float))}

    return CeremonyState(
        session_started=_bool("session_started"),
        checkpoint_count=_int("checkpoint_count"),
        last_checkpoint_ts=_opt_str("last_checkpoint_ts"),
        files_modified_since_checkpoint=_int("files_modified_since_checkpoint"),
        build_check_result=_opt_str("build_check_result"),
        deliver_called=_bool("deliver_called"),
        learnings_this_session=_int("learnings_this_session"),
        nudge_counts=nudge_counts,
        phase=_str("phase", "early"),
        # PRD-CORE-105 P0: previous_phase for transition detection
        previous_phase=_str("previous_phase", ""),
        # FR01 (PRD-CORE-084): review fields with fail-open defaults
        review_called=_bool("review_called"),
        review_verdict=_opt_str("review_verdict"),
        review_p0_count=_int("review_p0_count"),
        # PRD-CORE-103-FR02: nudge_history with fail-open empty dict
        nudge_history=_parse_nudge_history(data.get("nudge_history", {})),
        # PRD-CORE-129: Pool-based nudge tracking
        pool_nudge_counts=_dict_str_int("pool_nudge_counts"),
        pool_ignore_counts=_dict_str_int("pool_ignore_counts"),
        pool_cooldown_until=_dict_str_int("pool_cooldown_until"),
        tool_call_counter=_int("tool_call_counter"),
        last_nudge_pool=_str("last_nudge_pool", ""),
    )


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


def read_ceremony_state(trw_dir: Path) -> CeremonyState:
    """Read ceremony state from .trw/context/ceremony-state.json.

    Returns CeremonyState with defaults if the file is missing or corrupted
    (fail-open per NFR03 — never raises).
    """
    path = _state_path(trw_dir)
    if not path.exists():
        return CeremonyState()
    try:
        raw = path.read_text(encoding="utf-8")
        data: object = json.loads(raw)
        if not isinstance(data, dict):
            return CeremonyState()
        return _from_dict(data)
    except Exception:  # justified: fail-open boundary — corrupted/unreadable file returns defaults
        return CeremonyState()


def write_ceremony_state(trw_dir: Path, state: CeremonyState) -> None:
    """Atomically write ceremony state to .trw/context/ceremony-state.json.

    Uses temp-file + os.rename for POSIX atomicity (NFR04: < 10ms).
    Creates the context directory if it does not exist.
    """
    path = _state_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(asdict(state), separators=(",", ":"))
    # Write to a temp file in the same directory to guarantee same-filesystem rename
    fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, prefix=".ceremony-state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.rename(tmp_path_str, path)
    except Exception:  # justified: fail-open, ceremony state persistence is best-effort
        # Clean up the temp file on failure; do not propagate (fail-open)
        with contextlib.suppress(OSError):
            os.unlink(tmp_path_str)


def reset_ceremony_state(trw_dir: Path) -> None:
    """Reset ceremony state to defaults (called by trw_init)."""
    write_ceremony_state(trw_dir, CeremonyState())


# ---------------------------------------------------------------------------
# Update helpers
# ---------------------------------------------------------------------------


def mark_session_started(trw_dir: Path) -> None:
    """Set session_started = True."""
    state = read_ceremony_state(trw_dir)
    state.session_started = True
    write_ceremony_state(trw_dir, state)


def mark_checkpoint(trw_dir: Path) -> None:
    """Increment checkpoint_count, record timestamp, reset files_modified_since_checkpoint."""
    state = read_ceremony_state(trw_dir)
    state.checkpoint_count += 1
    state.last_checkpoint_ts = datetime.now(timezone.utc).isoformat()
    state.files_modified_since_checkpoint = 0
    write_ceremony_state(trw_dir, state)


def mark_build_check(trw_dir: Path, passed: bool) -> None:
    """Record the result of the most recent build check."""
    state = read_ceremony_state(trw_dir)
    state.build_check_result = "passed" if passed else "failed"
    write_ceremony_state(trw_dir, state)


def mark_deliver(trw_dir: Path) -> None:
    """Set deliver_called = True."""
    state = read_ceremony_state(trw_dir)
    state.deliver_called = True
    write_ceremony_state(trw_dir, state)


def mark_review(trw_dir: Path, verdict: str, p0_count: int = 0) -> None:
    """Set review_called = True, record verdict and P0 count (FR01, PRD-CORE-084)."""
    state = read_ceremony_state(trw_dir)
    state.review_called = True
    state.review_verdict = verdict
    state.review_p0_count = p0_count
    write_ceremony_state(trw_dir, state)


def set_ceremony_phase(trw_dir: Path, new_phase: str) -> None:
    """Update ceremony phase, preserving the old phase as previous_phase.

    PRD-CORE-105 P0: Tracks phase transitions for bandit burst detection.
    Only updates if the new phase differs from the current phase.
    """
    state = read_ceremony_state(trw_dir)
    if state.phase != new_phase:
        state.previous_phase = state.phase
        state.phase = new_phase
        write_ceremony_state(trw_dir, state)


def increment_files_modified(trw_dir: Path, count: int = 1) -> None:
    """Increment files_modified_since_checkpoint by *count*."""
    state = read_ceremony_state(trw_dir)
    state.files_modified_since_checkpoint += count
    write_ceremony_state(trw_dir, state)


def increment_learnings(trw_dir: Path) -> None:
    """Increment learnings_this_session by 1."""
    state = read_ceremony_state(trw_dir)
    state.learnings_this_session += 1
    write_ceremony_state(trw_dir, state)


def increment_nudge_count(trw_dir: Path, step: str) -> None:
    """Increment the nudge count for *step* by 1."""
    state = read_ceremony_state(trw_dir)
    state.nudge_counts[step] = state.nudge_counts.get(step, 0) + 1
    write_ceremony_state(trw_dir, state)


def reset_nudge_count(trw_dir: Path, step: str) -> None:
    """Reset the nudge count for *step* to 0."""
    state = read_ceremony_state(trw_dir)
    state.nudge_counts[step] = 0
    write_ceremony_state(trw_dir, state)


# ---------------------------------------------------------------------------
# Nudge history mutation helpers (PRD-CORE-103-FR02)
# ---------------------------------------------------------------------------

_NUDGE_HISTORY_CAP = 100  # Max entries per session


def record_nudge_shown(
    trw_dir: Path,
    learning_id: str,
    phase: str,
    turn: int = 0,
) -> None:
    """Record that a learning was shown as a nudge in the given phase.

    Updates nudge_history with dedup tracking. Enforces capacity cap
    by evicting the entry with the oldest last_shown_turn.
    """
    state = read_ceremony_state(trw_dir)

    if learning_id in state.nudge_history:
        entry = state.nudge_history[learning_id]
        if phase not in entry["phases_shown"]:
            entry["phases_shown"].append(phase)
        entry["last_shown_turn"] = turn
    else:
        # Enforce capacity cap
        if len(state.nudge_history) >= _NUDGE_HISTORY_CAP:
            # Evict oldest by last_shown_turn
            oldest_id = min(
                state.nudge_history,
                key=lambda k: state.nudge_history[k]["last_shown_turn"],
            )
            del state.nudge_history[oldest_id]

        state.nudge_history[learning_id] = NudgeHistoryEntry(
            phases_shown=[phase],
            turn_first_shown=turn,
            last_shown_turn=turn,
        )

    write_ceremony_state(trw_dir, state)


def clear_nudge_history(trw_dir: Path) -> None:
    """Clear nudge_history (called on context compaction detection)."""
    state = read_ceremony_state(trw_dir)
    state.nudge_history = {}
    write_ceremony_state(trw_dir, state)


def is_nudge_eligible(
    state: CeremonyState,
    learning_id: str,
    current_phase: str,
) -> bool:
    """Check if a learning is eligible for nudge display in the current phase.

    Returns False if the learning was already shown in this phase.
    Returns True if the learning was never shown, or only shown in other phases.
    """
    if learning_id not in state.nudge_history:
        return True
    entry = state.nudge_history[learning_id]
    return current_phase not in entry["phases_shown"]


# ---------------------------------------------------------------------------
# Pool-based nudge mutation helpers (PRD-CORE-129)
# ---------------------------------------------------------------------------


def increment_tool_call_counter(trw_dir: Path) -> None:
    """Increment the monotonic tool call counter by 1."""
    state = read_ceremony_state(trw_dir)
    state.tool_call_counter += 1
    write_ceremony_state(trw_dir, state)


def record_pool_nudge(trw_dir: Path, pool: str) -> None:
    """Record that a nudge was fired from the given pool."""
    state = read_ceremony_state(trw_dir)
    state.pool_nudge_counts[pool] = state.pool_nudge_counts.get(pool, 0) + 1
    state.last_nudge_pool = pool
    write_ceremony_state(trw_dir, state)


def record_pool_ignore(trw_dir: Path, pool: str) -> None:
    """Record that a nudge from the given pool was ignored."""
    state = read_ceremony_state(trw_dir)
    state.pool_ignore_counts[pool] = state.pool_ignore_counts.get(pool, 0) + 1
    write_ceremony_state(trw_dir, state)


# ---------------------------------------------------------------------------
# Step completion check (moved from _nudge_rules.py for PRD-CORE-089-FR02)
# ---------------------------------------------------------------------------


def _step_complete(step: str, state: CeremonyState) -> bool:
    """Return True if the given step is considered complete given *state*."""
    if step == "session_start":
        return state.session_started
    if step == "checkpoint":
        return state.checkpoint_count > 0 and state.files_modified_since_checkpoint <= 3
    if step == "build_check":
        return state.build_check_result == "passed"
    if step == "review":
        return state.review_called
    if step == "deliver":
        return state.deliver_called
    return False
