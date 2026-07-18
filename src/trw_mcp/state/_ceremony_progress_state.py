"""Neutral ceremony progress state storage shared by live and legacy paths."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from weakref import WeakValueDictionary

import structlog

from trw_mcp._locking import _lock_ex, _lock_un
from trw_mcp.state._ceremony_state_model import (
    CeremonyState as CeremonyState,
)
from trw_mcp.state._ceremony_state_model import (
    NudgeContext as NudgeContext,
)
from trw_mcp.state._ceremony_state_model import (
    NudgeHistoryEntry as NudgeHistoryEntry,
)
from trw_mcp.state._ceremony_state_model import (
    ToolName as ToolName,
)
from trw_mcp.state._ceremony_state_model import (
    _from_dict as _from_dict,
)
from trw_mcp.state._ceremony_state_model import (
    _parse_nudge_history as _parse_nudge_history,
)

logger = structlog.get_logger(__name__)

_STEPS: tuple[str, ...] = ("session_start", "checkpoint", "build_check", "review", "deliver")

# --- Concurrent read-modify-write protection (shared-HTTP server) -------------
# In shared-HTTP mode multiple MCP tool calls run on different threads of one
# process. Each ceremony mutator does an unguarded read -> mutate -> write of
# ceremony-state.json; without serialization two concurrent calls both read the
# stale state and the second os.replace() silently discards the first's update
# (e.g. a checkpoint increment is lost, or build_check_result is clobbered).
#
# Serialize every mutator's read-modify-write under a per-state-file lock keyed
# on the RESOLVED (absolute) state path, so all callers targeting the same file
# share one lock regardless of how ``trw_dir`` was spelled. The registry itself
# is guarded by ``_state_locks_guard``.
#
# Weak values keep the registry bounded without an eviction race: callers and
# waiters retain strong references to their lock, while idle project locks are
# reclaimed automatically.
_state_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_state_locks_guard = threading.Lock()


def _state_lock_for(trw_dir: Path) -> threading.Lock:
    """Return the process-wide lock guarding this state file's RMW cycle."""
    key = str(_state_path(trw_dir).resolve())
    with _state_locks_guard:
        lock = _state_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _state_locks[key] = lock
        return lock


@contextlib.contextmanager
def _state_rmw(trw_dir: Path) -> Iterator[None]:
    """Serialize a read-modify-write of the ceremony state for ``trw_dir``.

    Wrap the read_ceremony_state(...) -> mutate -> write_ceremony_state(...)
    body of every mutator so concurrent tool calls (shared-HTTP mode) cannot
    interleave and silently drop one another's updates.
    """
    thread_lock = _state_lock_for(trw_dir)
    lock_path = _state_path(trw_dir).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_ex(fd)
            yield
        finally:
            try:
                _lock_un(fd)
            finally:
                os.close(fd)


def _state_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-state.json"


def read_ceremony_state(trw_dir: Path) -> CeremonyState:
    """Read ceremony state. Missing/corrupt state fails open to defaults."""

    path = _state_path(trw_dir)
    if not path.exists():
        return CeremonyState()
    try:
        raw = path.read_text(encoding="utf-8")
        data: object = json.loads(raw)
        if not isinstance(data, dict):
            return CeremonyState()
        return _from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return CeremonyState()


def write_ceremony_state(trw_dir: Path, state: CeremonyState) -> None:
    """Atomically persist ceremony state."""

    path = _state_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(asdict(state), separators=(",", ":"))
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".ceremony-state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except OSError:
        # Persistence here is load-bearing: the delivery gate reads
        # build_check_result / session_started back from this file, so a silent
        # write failure mis-fires the gate (e.g. blocks a passing build). Stay
        # fail-open — a write failure must not crash a tool — but make it
        # VISIBLE per CONSTITUTION §PERSISTENCE instead of swallowing silently.
        logger.warning("ceremony_state_write_failed", state_path=str(path), exc_info=True)
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def reset_ceremony_state(trw_dir: Path) -> None:
    write_ceremony_state(trw_dir, CeremonyState())


def _touch_session_build_result(state: CeremonyState, session_id: str, result: str) -> None:
    """Record a session result and retain the most recently active sessions."""
    state.session_build_results.pop(session_id, None)
    state.session_build_results[session_id] = result
    while len(state.session_build_results) > 2048:
        state.session_build_results.pop(next(iter(state.session_build_results)))


def mark_session_started(trw_dir: Path, session_id: str | None = None) -> None:
    # PRD-FIX-076: write_ceremony_state() rewrites the file from the
    # CeremonyState dataclass (which does NOT carry ``mcp_never_connected_yet``),
    # so the sentinel written by init-project is cleared automatically as soon
    # as this function runs. Runs where MCP never connected keep the sentinel
    # because nothing else calls write_ceremony_state().
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.session_started = True
        if session_id:
            _touch_session_build_result(
                state,
                session_id,
                state.session_build_results.get(session_id, "pending"),
            )
        write_ceremony_state(trw_dir, state)


def mark_checkpoint(trw_dir: Path) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.checkpoint_count += 1
        state.last_checkpoint_ts = datetime.now(timezone.utc).isoformat()
        state.last_checkpoint_turn = state.tool_call_counter
        state.files_modified_since_checkpoint = 0
        write_ceremony_state(trw_dir, state)


def mark_build_check(trw_dir: Path, passed: bool, session_id: str | None = None) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.build_check_result = "passed" if passed else "failed"
        if session_id:
            _touch_session_build_result(state, session_id, state.build_check_result)
        state.last_build_check_ts = datetime.now(timezone.utc).isoformat()
        write_ceremony_state(trw_dir, state)


def mark_deliver(trw_dir: Path) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.deliver_called = True
        write_ceremony_state(trw_dir, state)


def mark_review(trw_dir: Path, verdict: str, p0_count: int = 0, *, substantive: bool = True) -> None:
    """Record REVIEW readiness without letting empty artifacts satisfy it."""
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.review_called = substantive
        state.review_verdict = verdict if substantive else "non_substantive"
        state.review_p0_count = p0_count if substantive else 0
        write_ceremony_state(trw_dir, state)


def set_ceremony_phase(trw_dir: Path, new_phase: str) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        if state.phase != new_phase:
            state.previous_phase = state.phase
            state.phase = new_phase
            write_ceremony_state(trw_dir, state)


def increment_files_modified(trw_dir: Path, count: int = 1) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.files_modified_since_checkpoint += count
        write_ceremony_state(trw_dir, state)


def increment_learnings(trw_dir: Path) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.learnings_this_session += 1
        write_ceremony_state(trw_dir, state)


def increment_nudge_count(trw_dir: Path, step: str) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.nudge_counts[step] = state.nudge_counts.get(step, 0) + 1
        write_ceremony_state(trw_dir, state)


def reset_nudge_count(trw_dir: Path, step: str) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.nudge_counts[step] = 0
        write_ceremony_state(trw_dir, state)


_NUDGE_HISTORY_CAP = 100


def record_nudge_shown(
    trw_dir: Path,
    learning_id: str,
    phase: str,
    turn: int,
    surface_type: str = "nudge",
) -> None:
    """Record a nudge impression.

    PRD-CORE-146-FR01: ``turn`` is REQUIRED (no default). Callers MUST pass
    ``CeremonyState.tool_call_counter`` at the moment of emission. A silent
    default of 0 previously caused every recorded entry to carry
    ``turn_first_shown=0`` / ``last_shown_turn=0``, which interacts
    pathologically with phase-crossing dedup and per-show accounting.

    PRD-QUAL-058-FR04: In addition to updating ceremony-state.json's nudge_history,
    this function also appends a discrete ``nudge_shown`` event to
    ``{trw_dir}/context/session-events.jsonl`` so downstream eval consumers
    can detect per-nudge activity from the event stream.

    Emission is best-effort: any failure in the session-event append is
    suppressed so the primary ceremony-state update always completes.
    """
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        if learning_id in state.nudge_history:
            entry = state.nudge_history[learning_id]
            if phase not in entry["phases_shown"]:
                entry["phases_shown"].append(phase)
            entry["last_shown_turn"] = turn
        else:
            if len(state.nudge_history) >= _NUDGE_HISTORY_CAP:
                oldest_id = min(state.nudge_history, key=lambda key: state.nudge_history[key]["last_shown_turn"])
                del state.nudge_history[oldest_id]
            state.nudge_history[learning_id] = NudgeHistoryEntry(
                phases_shown=[phase],
                turn_first_shown=turn,
                last_shown_turn=turn,
            )
        write_ceremony_state(trw_dir, state)

    # PRD-QUAL-058-FR04: Also emit a nudge_shown event to session-events.jsonl
    # so downstream eval consumers can detect per-nudge activity from the
    # event stream.
    _emit_nudge_shown_event(
        trw_dir,
        learning_id=learning_id,
        phase=phase,
        turn=turn,
        surface_type=surface_type,
    )


def _emit_nudge_shown_event(
    trw_dir: Path,
    *,
    learning_id: str,
    phase: str,
    turn: int,
    surface_type: str,
) -> None:
    """Append a nudge_shown event to session-events.jsonl.

    Fail-open: swallow any I/O / serialization error silently. The ceremony
    state update in the caller is the source-of-truth; this emission is an
    additive observability signal for the eval pipeline.
    """

    try:
        events_path = trw_dir / "context" / "session-events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        # PRD-CORE-146 NFR03: canonical contract fields consumed by downstream
        # eval consumers — ``step`` and ``learning_ids`` (plural list form). The
        # ``data.*`` block and the legacy singular ``learning_id`` / ``phase``
        # keys are preserved for backward compatibility with existing eval
        # consumers of the event stream.
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "nudge_shown",
            "step": phase,
            "learning_ids": [learning_id],
            "learning_id": learning_id,
            "phase": phase,
            "data": {
                "learning_id": learning_id,
                "phase": phase,
                "turn": turn,
                "surface_type": surface_type,
            },
        }
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError):
        # justified: fail-open — session event emission is best-effort
        return


def clear_nudge_history(trw_dir: Path) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.nudge_history = {}
        write_ceremony_state(trw_dir, state)


def is_nudge_eligible(state: CeremonyState, learning_id: str, current_phase: str) -> bool:
    if learning_id not in state.nudge_history:
        return True
    return current_phase not in state.nudge_history[learning_id]["phases_shown"]


def increment_tool_call_counter(trw_dir: Path) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.tool_call_counter += 1
        write_ceremony_state(trw_dir, state)


def record_pool_nudge(trw_dir: Path, pool: str) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.pool_nudge_counts[pool] = state.pool_nudge_counts.get(pool, 0) + 1
        state.last_nudge_pool = pool
        write_ceremony_state(trw_dir, state)


def record_pool_ignore(trw_dir: Path, pool: str) -> None:
    with _state_rmw(trw_dir):
        state = read_ceremony_state(trw_dir)
        state.pool_ignore_counts[pool] = state.pool_ignore_counts.get(pool, 0) + 1
        write_ceremony_state(trw_dir, state)


def _step_complete(step: str, state: CeremonyState) -> bool:
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
