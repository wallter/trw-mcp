"""Shared nudge gate for TRW Cursor hooks - anti-fatigue + adaptive skip + rotation.

Invoked from bash hook scripts with stdin=payload JSON and argv=[event_name,
cooldown_seconds, messages_json]. Prints a single JSON line on stdout: either
the gated user-visible response or ``{}`` when suppressed.

Three levers (C1-C3 from the eval-and-customizations research doc, applied at
the hook layer rather than the MCP layer so the UX-visible surface is
throttled independently of the MCP ceremony tools):

1. **Cooldown dedup** - `cursor-nudge-state.jsonl` records each emission keyed
   on (event_name, conversation_id). Re-fires within the cooldown window are
   suppressed. Default stop cooldown = 1 hour; session-start = 24 hours;
   pre-compact = 5 minutes (dedup per generation_id).

2. **Adaptive skip** - scans `cursor-hooks.jsonl` for the ceremony tool the
   nudge would prompt for. If already invoked in this conversation, the nudge
   is suppressed (the agent is already doing what we'd remind them to do).

3. **Message rotation** - when emission IS decided, one of several high-value
   messages is picked via sha256(conversation_id) % len(messages), so the same
   conversation sees a single consistent message but the population of
   conversations rotates through the full set.

Fails open: any exception during state reads or writes falls through to the
"do not emit" path (safer than emitting duplicates). The caller (bash hook)
can then decide whether to default to ``{}`` (observer) or a static fallback.

Argv:
    argv[1] = event_name (stop | sessionStart | preCompact | ...)
    argv[2] = cooldown_seconds (int)
    argv[3] = adaptive_skip_tool (MCP tool name or empty string to skip this check)
    argv[4] = response_key (followup_message | additional_context | user_message)
    argv[5] = messages_json (JSON array of strings - curated high-value set)

Example:
    payload_json | python3 _nudge_gate.py stop 3600 trw_deliver followup_message \\
        '["TRW: Before ending, call trw_deliver() - ...", "TRW: Wrap up with ..."]'
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TypedDict


class _HookPayload(TypedDict, total=False):
    conversation_id: str
    generation_id: str
    hook_event_name: str


class _NudgeStateEntry(TypedDict, total=False):
    ts: str
    event: str
    key: str


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> float:
    """Parse ISO-8601 UTC timestamp (with Z suffix) to unix epoch seconds."""
    try:
        dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return 0.0


def _read_payload() -> _HookPayload:
    """Parse the Cursor hook JSON payload from stdin."""
    try:
        raw = sys.stdin.read().strip()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed  # type: ignore[return-value]


def _log_dir() -> Path:
    base = os.environ.get("CURSOR_PROJECT_DIR", os.getcwd())
    return Path(base) / ".trw" / "logs"


def _state_file() -> Path:
    return _log_dir() / "cursor-nudge-state.jsonl"


def _hook_log_file() -> Path:
    return _log_dir() / "cursor-hooks.jsonl"


def _ensure_log_dir() -> None:
    with suppress(OSError):
        _log_dir().mkdir(parents=True, exist_ok=True)


def _in_cooldown(event_name: str, dedup_key: str, cooldown_seconds: int) -> bool:
    """Has this (event, key) pair emitted within the cooldown window?"""
    state_path = _state_file()
    if not state_path.is_file():
        return False
    now = time.time()
    try:
        # Read the tail — we only care about recent entries. For simplicity
        # read the whole file; the log is bounded by cooldown-era cleanup.
        with state_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") != event_name or rec.get("key") != dedup_key:
                    continue
                ts_str = rec.get("ts", "")
                if not isinstance(ts_str, str):
                    continue
                elapsed = now - _parse_iso(ts_str)
                if 0 <= elapsed < cooldown_seconds:
                    return True
    except OSError:
        return False
    return False


def _record_emission(event_name: str, dedup_key: str) -> None:
    """Append an emission record to the state file (fail-open)."""
    _ensure_log_dir()
    entry: _NudgeStateEntry = {
        "ts": _now_iso(),
        "event": event_name,
        "key": dedup_key,
    }
    try:
        with _state_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _ceremony_tool_already_invoked(adaptive_skip_tool: str, conversation_id: str) -> bool:
    """Has the MCP tool we'd prompt for already fired in this conversation?

    Scans cursor-hooks.jsonl for preToolUse entries where tool is
    ``MCP:<adaptive_skip_tool>``. No conversation scoping is applied because
    the current hook log doesn't record conversation_id in the line payload
    — instead we use a time-based heuristic: if the tool was invoked in the
    last 30 minutes, treat as "recent" and assume the agent has already
    completed the ceremony this user likely cares about.

    Returns False (do not suppress) when `adaptive_skip_tool` is empty or the
    log is unreadable — fail-open to the non-skipping path.
    """
    if not adaptive_skip_tool:
        return False
    log_path = _hook_log_file()
    if not log_path.is_file():
        return False
    tool_tag = f"tool=MCP:{adaptive_skip_tool}"
    lookback_seconds = 1800  # 30 minutes
    now = time.time()
    try:
        with log_path.open("r", encoding="utf-8") as f:
            # Read last ~1000 lines — enough for any reasonable session.
            lines = f.readlines()[-1000:]
    except OSError:
        return False
    # Parse each line as JSON and filter on structured fields rather than
    # substring matching — different json.dumps / printf formats produce
    # different whitespace around "event":"preToolUse", so a substring
    # needle is unreliable.
    for line in reversed(lines):
        if tool_tag not in line:
            # Fast-path reject: the msg field must contain the tool tag.
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "preToolUse":
            continue
        msg = rec.get("msg", "")
        if not isinstance(msg, str) or tool_tag not in msg:
            continue
        ts_str = rec.get("ts", "")
        if not isinstance(ts_str, str):
            continue
        elapsed = now - _parse_iso(ts_str)
        if 0 <= elapsed < lookback_seconds:
            return True
    return False


def _pick_message(messages: list[str], conversation_id: str) -> str:
    """Stable message selection per conversation — deterministic rotation."""
    if not messages:
        return ""
    if not conversation_id:
        conversation_id = "default"
    h = int(hashlib.sha256(conversation_id.encode()).hexdigest()[:8], 16)
    return messages[h % len(messages)]


def main(argv: list[str]) -> int:
    if len(argv) < 6:
        print("{}")
        return 0

    event_name = argv[1]
    try:
        cooldown_seconds = int(argv[2])
    except ValueError:
        cooldown_seconds = 3600
    adaptive_skip_tool = argv[3]
    response_key = argv[4]
    try:
        messages = json.loads(argv[5])
        if not isinstance(messages, list):
            messages = []
    except json.JSONDecodeError:
        messages = []

    payload = _read_payload()
    conversation_id = str(payload.get("conversation_id", "") or "default")
    # pre-compact prefers generation_id (one per turn) for finer dedup
    if event_name == "preCompact":
        dedup_key = str(payload.get("generation_id", "") or conversation_id)
    else:
        dedup_key = conversation_id

    # Gate 1: cooldown
    if _in_cooldown(event_name, dedup_key, cooldown_seconds):
        print("{}")
        return 0

    # Gate 2: adaptive skip (ceremony tool already invoked)
    if _ceremony_tool_already_invoked(adaptive_skip_tool, conversation_id):
        print("{}")
        return 0

    # Emit: pick a rotated high-value message, record, and print.
    message = _pick_message(messages, conversation_id)
    if not message:
        print("{}")
        return 0

    _record_emission(event_name, dedup_key)
    print(json.dumps({response_key: message}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
