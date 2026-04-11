"""Neutral ceremony progress state storage shared by live and legacy paths."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

_STEPS: tuple[str, ...] = ("session_start", "checkpoint", "build_check", "review", "deliver")


class NudgeHistoryEntry(TypedDict):
    """Record of when/where a learning was shown as a legacy nudge."""

    phases_shown: list[str]
    turn_first_shown: int
    last_shown_turn: int


@dataclass
class CeremonyState:
    """Tracks ceremony progress for the current session."""

    session_started: bool = False
    checkpoint_count: int = 0
    last_checkpoint_ts: str | None = None
    files_modified_since_checkpoint: int = 0
    build_check_result: str | None = None
    deliver_called: bool = False
    learnings_this_session: int = 0
    nudge_counts: dict[str, int] = field(default_factory=dict)
    phase: str = "early"
    previous_phase: str = ""
    review_called: bool = False
    review_verdict: str | None = None
    review_p0_count: int = 0
    nudge_history: dict[str, NudgeHistoryEntry] = field(default_factory=dict)
    pool_nudge_counts: dict[str, int] = field(default_factory=dict)
    pool_ignore_counts: dict[str, int] = field(default_factory=dict)
    pool_cooldown_until: dict[str, int] = field(default_factory=dict)
    tool_call_counter: int = 0
    last_nudge_pool: str = ""


@dataclass
class NudgeContext:
    """Legacy nudge context preserved for offline compatibility."""

    tool_name: str = ""
    tool_success: bool = True
    build_passed: bool | None = None
    review_verdict: str | None = None
    review_p0_count: int = 0
    is_subagent: bool = False


class ToolName:
    """Constants for legacy nudge tool names."""

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


def _state_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-state.json"


def _parse_nudge_history(raw: object) -> dict[str, NudgeHistoryEntry]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, NudgeHistoryEntry] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        try:
            result[key] = NudgeHistoryEntry(
                phases_shown=[str(p) for p in val.get("phases_shown", []) if isinstance(p, str)],
                turn_first_shown=int(val.get("turn_first_shown", 0)),
                last_shown_turn=int(val.get("last_shown_turn", 0)),
            )
        except (TypeError, ValueError):
            continue
    return result


def _from_dict(data: dict[str, object]) -> CeremonyState:
    def _bool(key: str, default: bool = False) -> bool:
        value = data.get(key, default)
        return bool(value) if isinstance(value, bool) else default

    def _int(key: str, default: int = 0) -> int:
        value = data.get(key, default)
        return int(value) if isinstance(value, (int, float)) else default

    def _str(key: str, default: str = "") -> str:
        value = data.get(key, default)
        return str(value) if isinstance(value, str) else default

    def _opt_str(key: str) -> str | None:
        value = data.get(key)
        return str(value) if isinstance(value, str) else None

    def _dict_str_int(key: str) -> dict[str, int]:
        raw = data.get(key, {})
        if not isinstance(raw, dict):
            return {}
        return {k: int(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, (int, float))}

    nudge_raw = data.get("nudge_counts", {})
    nudge_counts = (
        {k: v for k, v in nudge_raw.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(nudge_raw, dict)
        else {}
    )

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
        previous_phase=_str("previous_phase", ""),
        review_called=_bool("review_called"),
        review_verdict=_opt_str("review_verdict"),
        review_p0_count=_int("review_p0_count"),
        nudge_history=_parse_nudge_history(data.get("nudge_history", {})),
        pool_nudge_counts=_dict_str_int("pool_nudge_counts"),
        pool_ignore_counts=_dict_str_int("pool_ignore_counts"),
        pool_cooldown_until=_dict_str_int("pool_cooldown_until"),
        tool_call_counter=_int("tool_call_counter"),
        last_nudge_pool=_str("last_nudge_pool", ""),
    )


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
        os.rename(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def reset_ceremony_state(trw_dir: Path) -> None:
    write_ceremony_state(trw_dir, CeremonyState())


def mark_session_started(trw_dir: Path) -> None:
    state = read_ceremony_state(trw_dir)
    state.session_started = True
    write_ceremony_state(trw_dir, state)


def mark_checkpoint(trw_dir: Path) -> None:
    state = read_ceremony_state(trw_dir)
    state.checkpoint_count += 1
    state.last_checkpoint_ts = datetime.now(timezone.utc).isoformat()
    state.files_modified_since_checkpoint = 0
    write_ceremony_state(trw_dir, state)


def mark_build_check(trw_dir: Path, passed: bool) -> None:
    state = read_ceremony_state(trw_dir)
    state.build_check_result = "passed" if passed else "failed"
    write_ceremony_state(trw_dir, state)


def mark_deliver(trw_dir: Path) -> None:
    state = read_ceremony_state(trw_dir)
    state.deliver_called = True
    write_ceremony_state(trw_dir, state)


def mark_review(trw_dir: Path, verdict: str, p0_count: int = 0) -> None:
    state = read_ceremony_state(trw_dir)
    state.review_called = True
    state.review_verdict = verdict
    state.review_p0_count = p0_count
    write_ceremony_state(trw_dir, state)


def set_ceremony_phase(trw_dir: Path, new_phase: str) -> None:
    state = read_ceremony_state(trw_dir)
    if state.phase != new_phase:
        state.previous_phase = state.phase
        state.phase = new_phase
        write_ceremony_state(trw_dir, state)


def increment_files_modified(trw_dir: Path, count: int = 1) -> None:
    state = read_ceremony_state(trw_dir)
    state.files_modified_since_checkpoint += count
    write_ceremony_state(trw_dir, state)


def increment_learnings(trw_dir: Path) -> None:
    state = read_ceremony_state(trw_dir)
    state.learnings_this_session += 1
    write_ceremony_state(trw_dir, state)


def increment_nudge_count(trw_dir: Path, step: str) -> None:
    state = read_ceremony_state(trw_dir)
    state.nudge_counts[step] = state.nudge_counts.get(step, 0) + 1
    write_ceremony_state(trw_dir, state)


def reset_nudge_count(trw_dir: Path, step: str) -> None:
    state = read_ceremony_state(trw_dir)
    state.nudge_counts[step] = 0
    write_ceremony_state(trw_dir, state)


_NUDGE_HISTORY_CAP = 100


def record_nudge_shown(trw_dir: Path, learning_id: str, phase: str, turn: int = 0) -> None:
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


def clear_nudge_history(trw_dir: Path) -> None:
    state = read_ceremony_state(trw_dir)
    state.nudge_history = {}
    write_ceremony_state(trw_dir, state)


def is_nudge_eligible(state: CeremonyState, learning_id: str, current_phase: str) -> bool:
    if learning_id not in state.nudge_history:
        return True
    return current_phase not in state.nudge_history[learning_id]["phases_shown"]


def increment_tool_call_counter(trw_dir: Path) -> None:
    state = read_ceremony_state(trw_dir)
    state.tool_call_counter += 1
    write_ceremony_state(trw_dir, state)


def record_pool_nudge(trw_dir: Path, pool: str) -> None:
    state = read_ceremony_state(trw_dir)
    state.pool_nudge_counts[pool] = state.pool_nudge_counts.get(pool, 0) + 1
    state.last_nudge_pool = pool
    write_ceremony_state(trw_dir, state)


def record_pool_ignore(trw_dir: Path, pool: str) -> None:
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
