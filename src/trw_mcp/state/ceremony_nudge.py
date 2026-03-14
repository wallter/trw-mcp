"""Ceremony State Tracker for Universal Ceremony Enforcement (PRD-CORE-074 FR04).

Tracks what ceremony steps have been completed in the current session.
Persisted as JSON at .trw/context/ceremony-state.json.

Design constraints:
- All reads are fail-open: missing or corrupted file returns defaults, never raises.
- Writes are atomic: write to temp file then os.rename (POSIX atomic on same filesystem).
- JSON format (not YAML) for fast parsing.
- No external dependencies beyond stdlib + dataclasses.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------

@dataclass
class CeremonyState:
    """Tracks ceremony step completion for the current session."""

    session_started: bool = False
    checkpoint_count: int = 0
    last_checkpoint_ts: str | None = None        # ISO timestamp
    files_modified_since_checkpoint: int = 0
    build_check_result: str | None = None        # "passed" | "failed" | None
    deliver_called: bool = False
    learnings_this_session: int = 0
    nudge_counts: dict[str, int] = field(default_factory=dict)  # step -> nudge count
    phase: str = "early"  # early, implement, validate, deliver, done


# ---------------------------------------------------------------------------
# File path helper
# ---------------------------------------------------------------------------

def _state_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-state.json"


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
    fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=".ceremony-state-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.rename(tmp_path_str, path)
    except Exception:  # justified: fail-open, ceremony state persistence is best-effort
        # Clean up the temp file on failure; do not propagate (fail-open)
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass


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
# FR01: Nudge computation engine (PRD-CORE-074)
# ---------------------------------------------------------------------------

_HEADER = "--- TRW Session ---"

# Step names in display order
_STEPS = ("session_start", "checkpoint", "build_check", "deliver")


def _step_complete(step: str, state: CeremonyState) -> bool:
    """Return True if the given step is considered complete given *state*."""
    if step == "session_start":
        return state.session_started
    if step == "checkpoint":
        return state.checkpoint_count > 0 and state.files_modified_since_checkpoint <= 3
    if step == "build_check":
        return state.build_check_result == "passed"
    if step == "deliver":
        return state.deliver_called
    return False


def _build_status_line(state: CeremonyState) -> str:
    """Build the checkmark/cross status line for all ceremony steps.

    Format:  ✓ session_start | ✗ checkpoint (5 files modified, 12 min since start)
    """
    parts: list[str] = []
    for step in _STEPS:
        mark = "\u2713" if _step_complete(step, state) else "\u2717"
        label = step

        # Add contextual annotation for incomplete steps
        if step == "checkpoint" and not _step_complete(step, state):
            n = state.files_modified_since_checkpoint
            if n > 0:
                label = f"checkpoint ({n} files modified)"
            else:
                label = "checkpoint (no checkpoint yet)"
        elif step == "build_check" and not _step_complete(step, state):
            phase = state.phase
            if phase not in ("validate", "deliver", "done"):
                # Not yet at the phase — show without annotation
                label = "build_check"
        elif step == "deliver" and state.learnings_this_session > 0 and not state.deliver_called:
            label = f"deliver ({state.learnings_this_session} learnings pending)"

        parts.append(f"{mark} {label}")

    return " | ".join(parts)


def _compute_urgency(state: CeremonyState, step: str) -> str:
    """Return urgency level based on how many times this step has been nudged.

    Returns: 'low' (0-2 nudges), 'medium' (3-4 nudges), or 'high' (5+ nudges).
    """
    count = state.nudge_counts.get(step, 0)
    if count >= 5:
        return "high"
    return "medium" if count >= 3 else "low"


def _select_message_by_urgency(
    urgency: str,
    low: str,
    medium: str,
    high: str,
) -> str:
    """Select a message template based on urgency level.

    Used internally by _select_nudge_message to DRY message selection.
    """
    if urgency == "high":
        return high
    return medium if urgency == "medium" else low


def _select_nudge_message(
    step: str, state: CeremonyState, available_learnings: int
) -> str:
    """Select the value-expressing nudge message for the given step.

    Messages follow the value-expression template (FR02):
      fact -> value -> consequence -> effort framing.
    No prescriptive language ("MUST", "CRITICAL", etc.) or decision language.

    Progressive urgency (FR03): messages grow more specific based on nudge_counts[step].
    """
    urgency = _compute_urgency(state, step)

    if step == "session_start":
        n = available_learnings
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} prior learnings load in 1s — "
                    "past discoveries become active context. "
                    "Call trw_session_start() to begin."
                ),
                medium=(
                    f"\u26a1 {n} prior learnings load in 1s — "
                    f"each skipped loading costs future agents {n} re-discoveries. "
                    "Call trw_session_start() to begin."
                ),
                high=(
                    f"\u26a1 {n} prior learnings available — "
                    "past discoveries become active context, preventing repeat mistakes. "
                    f"Skipping means re-discovering what {n} prior sessions already learned. "
                    "trw_session_start() takes 1s."
                ),
            )
        return _select_message_by_urgency(
            urgency,
            low=(
                "\u26a1 Session tracking starts with trw_session_start() — "
                "progress, checkpoints, and learnings attach to this run."
            ),
            medium=(
                "\u26a1 Session tracking not started — "
                "progress and learnings won't persist without it. "
                "trw_session_start() wires them to this run."
            ),
            high=(
                "\u26a1 Session tracking not started — "
                "progress, checkpoints, and learnings are unattached to this run. "
                "Without it, this session's work is invisible to future agents. "
                "trw_session_start() takes 1s."
            ),
        )

    if step == "checkpoint":
        n = state.files_modified_since_checkpoint
        # Compute elapsed time since last checkpoint for contextual display
        elapsed = ""
        if state.last_checkpoint_ts:
            try:
                last = datetime.fromisoformat(state.last_checkpoint_ts)
                now = datetime.now(timezone.utc)
                mins = int((now - last).total_seconds() / 60)
                if mins > 0:
                    elapsed = f", {mins} min ago"
            except (ValueError, TypeError):
                pass
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} files modified since last checkpoint{elapsed} — "
                    "context compaction would lose this progress. "
                    "trw_checkpoint() saves it in under 2s."
                ),
                medium=(
                    f"\u26a1 {n} files modified since last checkpoint{elapsed} — "
                    f"compaction risk: {n} file(s) of progress lost with no recovery path. "
                    "trw_checkpoint() saves it in under 2s."
                ),
                high=(
                    f"\u26a1 {n} files modified since last checkpoint{elapsed} — "
                    f"context compaction erases all {n} changes permanently. "
                    "trw_checkpoint() saves everything in 2 seconds."
                ),
            )
        return _select_message_by_urgency(
            urgency,
            low=(
                f"\u26a1 No checkpoint in this session yet{elapsed} — "
                "a checkpoint saves state so context compaction can resume here. "
                "trw_checkpoint() takes under 2s."
            ),
            medium=(
                f"\u26a1 No checkpoint yet this session{elapsed} — "
                "context compaction would lose all progress with no recovery path. "
                "trw_checkpoint() takes under 2s."
            ),
            high=(
                f"\u26a1 No checkpoint in this session{elapsed} — "
                "all session progress is unrecoverable if context compacts. "
                "trw_checkpoint() anchors it in 2 seconds."
            ),
        )

    if step == "build_check":
        return _select_message_by_urgency(
            urgency,
            low=(
                "\u26a1 Build check not run yet — "
                "tests + type-check catches integration issues before delivery. "
                "trw_build_check() runs the full gate."
            ),
            medium=(
                "\u26a1 Build check not run — "
                "type errors and test failures are undetected; delivery ships them as-is. "
                "trw_build_check() runs the full gate."
            ),
            high=(
                "\u26a1 Build check not run — "
                "integration issues delivered without verification stay broken in production. "
                "trw_build_check() catches them in under 2 minutes."
            ),
        )

    if step == "deliver":
        n = state.learnings_this_session
        if n > 0:
            return _select_message_by_urgency(
                urgency,
                low=(
                    f"\u26a1 {n} learning(s) recorded this session — "
                    "trw_deliver() persists them for all future sessions. "
                    "Lost if skipped."
                ),
                medium=(
                    f"\u26a1 {n} learning(s) recorded this session — "
                    f"skipping trw_deliver() discards all {n}; future agents lose this context. "
                    "trw_deliver() persists them for all future sessions."
                ),
                high=(
                    f"\u26a1 {n} learning(s) recorded this session — "
                    f"all {n} are lost permanently if the session ends without trw_deliver(). "
                    "Future agents re-learn them from scratch. Takes 2 seconds."
                ),
            )
        return _select_message_by_urgency(
            urgency,
            low=(
                "\u26a1 Session complete — "
                "trw_deliver() persists the run and any learnings for future sessions."
            ),
            medium=(
                "\u26a1 Session complete but not delivered — "
                "run record won't persist for future sessions without trw_deliver()."
            ),
            high=(
                "\u26a1 Session complete but not delivered — "
                "the run record and any learnings are unattached until trw_deliver() is called. "
                "Takes 2 seconds."
            ),
        )

    return ""


def _highest_priority_pending_step(state: CeremonyState) -> str | None:
    """Return the highest-priority pending step name, or None if all done."""
    # Priority 1: session_start
    if not state.session_started:
        return "session_start"

    # Priority 2: checkpoint (if files modified > 3 OR no checkpoint in session)
    needs_checkpoint = (
        state.files_modified_since_checkpoint > 3
        or state.checkpoint_count == 0
    )
    if needs_checkpoint:
        return "checkpoint"

    # Priority 3: build_check (if phase >= validate and not run)
    if state.phase in ("validate", "deliver", "done") and state.build_check_result != "passed":
        return "build_check"

    # Priority 4: deliver (if phase >= deliver)
    if state.phase in ("deliver", "done") and not state.deliver_called:
        return "deliver"

    return None


def compute_nudge(state: CeremonyState, available_learnings: int = 0) -> str:
    """Compute the ceremony nudge message based on current state.

    Priority order:
    1. session_start (if not called)
    2. checkpoint (if files_modified > 3 or no checkpoint in session)
    3. build_check (if phase >= validate and not run)
    4. deliver (if phase >= deliver)
    5. None (all complete — minimal status line)

    Returns:
        Nudge string to append to tool responses. Empty string if any error occurs.
        Never exceeds 100 tokens (~400 chars). Never blocks or refuses.
    """
    try:
        status_line = _build_status_line(state)
        pending = _highest_priority_pending_step(state)

        if pending is None:
            # All complete — single line
            return f"{_HEADER}\n{status_line}"

        nudge_msg = _select_nudge_message(pending, state, available_learnings)
        full = f"{_HEADER}\n{status_line}\n{nudge_msg}"

        # Enforce token limit (~400 chars)
        if len(full) > 400:
            full = full[:397] + "..."

        return full
    except Exception:  # justified: fail-open — nudge must never raise or block tool responses
        return ""


# ---------------------------------------------------------------------------
# FR12: Local model detection and minimal ceremony nudge (PRD-CORE-074)
# ---------------------------------------------------------------------------

_MINIMAL_HEADER = "--- TRW ---"


def is_local_model(model_id: str) -> bool:
    """Detect if a model ID indicates a local model.

    Local model indicators:
    - Starts with "ollama/"
    - Starts with "local/"
    - Contains "localhost"
    """
    model_lower = model_id.lower()
    return (
        model_lower.startswith("ollama/")
        or model_lower.startswith("local/")
        or "localhost" in model_lower
    )


def _build_minimal_status_line(state: CeremonyState) -> str:
    """Build a compact status line covering only session_start and deliver."""
    start_mark = "\u2713" if state.session_started else "\u2717"
    deliver_mark = "\u2713" if state.deliver_called else "\u2717"
    return f"{start_mark} start | {deliver_mark} deliver"


def compute_nudge_minimal(state: CeremonyState, available_learnings: int = 0) -> str:
    """Compute a minimal ceremony nudge for local models.

    MINIMAL ceremony only nudges for session_start and deliver.
    Messages are capped at 50 tokens (~200 chars) instead of 100 tokens.
    Never raises (fail-open).
    """
    try:
        status_line = _build_minimal_status_line(state)

        # Determine the single pending step (only session_start or deliver)
        if not state.session_started:
            pending = "session_start"
        elif not state.deliver_called:
            pending = "deliver"
        else:
            pending = None

        if pending is None:
            # All complete — single compact line (well under 80 chars)
            return f"{_MINIMAL_HEADER}\n{status_line}"

        # Build a short message under 200 chars total
        if pending == "session_start":
            n = available_learnings
            if n > 0:
                msg = f"\u26a1 {n} prior learnings available. Call trw_session_start()."
            else:
                msg = "\u26a1 Call trw_session_start() to begin."
        else:  # deliver
            n = state.learnings_this_session
            if n > 0:
                msg = f"\u26a1 {n} learning(s) pending. Call trw_deliver() to persist."
            else:
                msg = "\u26a1 Call trw_deliver() to persist this session."

        full = f"{_MINIMAL_HEADER}\n{status_line}\n{msg}"

        # Enforce 200-char cap
        if len(full) > 200:
            full = full[:197] + "..."

        return full
    except Exception:  # justified: fail-open — nudge must never raise or block tool responses
        return ""


# ---------------------------------------------------------------------------
# Internal deserialization helper
# ---------------------------------------------------------------------------

def _from_dict(data: dict[str, object]) -> CeremonyState:
    """Deserialize a CeremonyState from a plain dict.

    Unknown or malformed fields are silently ignored (fail-open).
    """
    nudge_raw = data.get("nudge_counts", {})
    nudge_counts: dict[str, int] = {}
    if isinstance(nudge_raw, dict):
        for k, v in nudge_raw.items():
            if isinstance(k, str) and isinstance(v, int):
                nudge_counts[k] = v

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
    )
