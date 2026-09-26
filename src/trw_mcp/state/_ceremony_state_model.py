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
class PoolCooldown:
    """One pool's ignore count and counter/wall-clock cooldown."""

    ignore_count: int = 0
    until_counter: int = 0
    set_at: str = ""


@dataclass
class CeremonyState:
    """Tracks ceremony progress for the current session."""

    session_started: bool = False
    session_build_results: dict[str, str] = field(default_factory=dict)
    #: ISO timestamp of each session's most recent recorded build result; a
    #: pass is only evidence for the tree as it stood then (release-verify
    #: 2026-09-17 P1-1).
    session_build_results_at: dict[str, str] = field(default_factory=dict)
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
    pool_cooldowns: dict[str, PoolCooldown] = field(default_factory=dict)
    tool_call_counter: int = 0
    last_nudge_pool: str = ""
    #: PRD-CORE-294 FR04/FR06 transition-nudge selector state, keyed by
    #: session key. Each value: {"shown_ids": list[str], "count": int,
    #: "last_counter": int} — dedup, per-session budget, and the cooldown
    #: anchor (compared against ``tool_call_counter``). Capped like
    #: ``session_build_results`` (oldest evicted at 2048 sessions).
    transition_nudges: dict[str, dict[str, object]] = field(default_factory=dict)


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

    def _pool_cooldowns() -> dict[str, PoolCooldown]:
        legacy = {"pool_ignore_counts", "pool_cooldown_until", "pool_cooldown_set_at"}
        if legacy.intersection(data):
            raise ValueError("schema_mismatch: legacy pool cooldown fields")
        if "pool_cooldowns" not in data:
            raise ValueError("schema_mismatch: missing pool_cooldowns")
        raw = data["pool_cooldowns"]
        if not isinstance(raw, dict):
            raise TypeError("schema_mismatch: pool_cooldowns must be an object")
        result: dict[str, PoolCooldown] = {}
        for pool, value in raw.items():
            if not isinstance(pool, str) or not isinstance(value, dict):
                raise TypeError("schema_mismatch: malformed pool cooldown")
            ignore, until, set_at = value.get("ignore_count"), value.get("until_counter"), value.get("set_at")
            if (
                not isinstance(ignore, int)
                or isinstance(ignore, bool)
                or not isinstance(until, int)
                or isinstance(until, bool)
                or not isinstance(set_at, str)
            ):
                raise TypeError("schema_mismatch: malformed pool cooldown")
            result[pool] = PoolCooldown(ignore, until, set_at)
        return result

    def _transition_nudges(key: str) -> dict[str, dict[str, object]]:
        raw = data.get(key, {})
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, object]] = {}
        for session_key, entry in raw.items():
            if not isinstance(session_key, str) or not isinstance(entry, dict):
                continue
            shown_ids_raw = entry.get("shown_ids", [])
            shown_ids = (
                [item for item in shown_ids_raw if isinstance(item, str)] if isinstance(shown_ids_raw, list) else []
            )
            count = entry.get("count", 0)
            last_counter = entry.get("last_counter", 0)
            result[session_key] = {
                "shown_ids": shown_ids,
                "count": int(count) if isinstance(count, (int, float)) else 0,
                "last_counter": int(last_counter) if isinstance(last_counter, (int, float)) else 0,
            }
        return result

    raw_counts = data.get("nudge_counts", {})
    nudge_counts = (
        {k: v for k, v in raw_counts.items() if isinstance(k, str) and isinstance(v, int)}
        if isinstance(raw_counts, dict)
        else {}
    )
    return CeremonyState(
        session_started=_bool("session_started"),
        session_build_results=_dict_str_str("session_build_results"),
        session_build_results_at=_dict_str_str("session_build_results_at"),
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
        pool_cooldowns=_pool_cooldowns(),
        tool_call_counter=_int("tool_call_counter"),
        last_nudge_pool=_str("last_nudge_pool"),
        transition_nudges=_transition_nudges("transition_nudges"),
    )


# Private alias retained because legacy tests import it through _nudge_state.
_from_dict = ceremony_state_from_dict
