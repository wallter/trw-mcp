"""Ceremony progress value objects and their defensive JSON decoder."""

from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import TypedDict


class NudgeHistoryEntry(TypedDict):
    """Record of when/where a learning was shown as a legacy nudge."""

    phases_shown: list[str]
    turn_first_shown: int
    last_shown_turn: int


@dataclass
class CeremonyState:
    """Tracks ceremony progress for the current session."""

    session_started: bool = False
    session_build_results: dict[str, str] = field(default_factory=dict)
    checkpoint_count: int = 0
    last_checkpoint_ts: str | None = None
    last_checkpoint_turn: int = 0
    files_modified_since_checkpoint: int = 0
    build_check_result: str | None = None
    last_build_check_ts: str | None = None
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
    # Wall-clock timestamp when a pool entered cooldown (PRD-CORE-144 FR03).
    pool_cooldown_set_at: dict[str, str] = field(default_factory=dict)
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


def ceremony_state_from_dict(data: dict[str, object]) -> CeremonyState:
    """Decode persisted state while failing open on malformed field values."""

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

    def _dict_str_str(key: str) -> dict[str, str]:
        raw = data.get(key, {})
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}

    raw_counts = data.get("nudge_counts", {})
    nudge_counts = (
        {k: v for k, v in raw_counts.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(raw_counts, dict)
        else {}
    )
    return CeremonyState(
        session_started=_bool("session_started"),
        session_build_results=_dict_str_str("session_build_results"),
        checkpoint_count=_int("checkpoint_count"),
        last_checkpoint_ts=_opt_str("last_checkpoint_ts"),
        last_checkpoint_turn=_int("last_checkpoint_turn"),
        files_modified_since_checkpoint=_int("files_modified_since_checkpoint"),
        build_check_result=_opt_str("build_check_result"),
        last_build_check_ts=_opt_str("last_build_check_ts"),
        deliver_called=_bool("deliver_called"),
        learnings_this_session=_int("learnings_this_session"),
        nudge_counts=nudge_counts,
        phase=_str("phase", "early"),
        previous_phase=_str("previous_phase"),
        review_called=_bool("review_called"),
        review_verdict=_opt_str("review_verdict"),
        review_p0_count=_int("review_p0_count"),
        nudge_history=_parse_nudge_history(data.get("nudge_history", {})),
        pool_nudge_counts=_dict_str_int("pool_nudge_counts"),
        pool_ignore_counts=_dict_str_int("pool_ignore_counts"),
        pool_cooldown_until=_dict_str_int("pool_cooldown_until"),
        pool_cooldown_set_at=_dict_str_str("pool_cooldown_set_at"),
        tool_call_counter=_int("tool_call_counter"),
        last_nudge_pool=_str("last_nudge_pool"),
    )


# Private alias retained because legacy tests import it through _nudge_state.
_from_dict = ceremony_state_from_dict
