"""Tests for the shared Cursor hook nudge gate (_nudge_gate.py).

The gate is invoked from bash hook scripts (trw-stop.sh, trw-session-start.sh,
trw-pre-compact.sh) to apply three levers before emitting a user-visible
followup_message / additional_context / user_message:

  1. Anti-fatigue cooldown — dedup per (event, conversation_id) within a window
  2. Adaptive skip — suppress when the ceremony tool the nudge prompts for
     has already been invoked in the recent hook log
  3. Message rotation — stable per-conversation selection from a curated set

Tests drive the script as a subprocess so the full bash → python → stdout
pipeline is exercised. CURSOR_PROJECT_DIR is pinned to tmp_path so the
state + log files live in a hermetic location.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

import pytest


_GATE_SCRIPT = (
    Path(__file__).parent.parent
    / "src" / "trw_mcp" / "data" / "hooks" / "cursor" / "_nudge_gate.py"
)
_STOP_SCRIPT = (
    Path(__file__).parent.parent
    / "src" / "trw_mcp" / "data" / "hooks" / "cursor" / "trw-stop.sh"
)
_SESSION_START_SCRIPT = (
    Path(__file__).parent.parent
    / "src" / "trw_mcp" / "data" / "hooks" / "cursor" / "trw-session-start.sh"
)
_PRE_COMPACT_SCRIPT = (
    Path(__file__).parent.parent
    / "src" / "trw_mcp" / "data" / "hooks" / "cursor" / "trw-pre-compact.sh"
)


def _run_gate(
    *,
    tmp_path: Path,
    payload: dict,
    event_name: str,
    cooldown: int,
    adaptive_tool: str,
    response_key: str,
    messages: list[str],
) -> dict:
    """Invoke _nudge_gate.py directly and return parsed stdout."""
    env = {"CURSOR_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [
            sys.executable,
            str(_GATE_SCRIPT),
            event_name,
            str(cooldown),
            adaptive_tool,
            response_key,
            json.dumps(messages),
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip() or "{}")


def _run_hook(script: Path, *, tmp_path: Path, payload: dict) -> dict:
    """Invoke a bash hook script end-to-end."""
    env = {"CURSOR_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip() or "{}")


@pytest.mark.integration
class TestCooldownDedup:
    """Anti-fatigue: repeat emissions within the cooldown window are suppressed."""

    def test_second_call_within_cooldown_suppresses(self, tmp_path: Path) -> None:
        """Two calls with same (event, conversation) → first emits, second is {}."""
        payload = {"conversation_id": "convo-1"}
        first = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="stop",
            cooldown=3600, adaptive_tool="", response_key="followup_message",
            messages=["MSG-A"],
        )
        second = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="stop",
            cooldown=3600, adaptive_tool="", response_key="followup_message",
            messages=["MSG-A"],
        )

        assert first == {"followup_message": "MSG-A"}
        assert second == {}

    def test_different_conversation_ids_both_emit(self, tmp_path: Path) -> None:
        """Independent conversations each get one emission."""
        r1 = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c-1"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key="followup_message", messages=["X"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c-2"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key="followup_message", messages=["X"],
        )
        assert r1 == {"followup_message": "X"}
        assert r2 == {"followup_message": "X"}

    def test_zero_cooldown_never_dedups(self, tmp_path: Path) -> None:
        """cooldown=0 → every call emits (disables dedup)."""
        payload = {"conversation_id": "c"}
        r1 = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="stop", cooldown=0,
            adaptive_tool="", response_key="followup_message", messages=["X"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="stop", cooldown=0,
            adaptive_tool="", response_key="followup_message", messages=["X"],
        )
        assert r1 == {"followup_message": "X"}
        assert r2 == {"followup_message": "X"}

    def test_expired_cooldown_re_emits(self, tmp_path: Path) -> None:
        """Backdating the state entry past the cooldown window → next call emits."""
        payload = {"conversation_id": "c"}
        _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="stop", cooldown=60,
            adaptive_tool="", response_key="followup_message", messages=["X"],
        )
        # Backdate the state entry
        state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
        lines = state_file.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=7200)
        rec["ts"] = past.strftime("%Y-%m-%dT%H:%M:%SZ")
        state_file.write_text(json.dumps(rec) + "\n")

        r2 = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="stop", cooldown=60,
            adaptive_tool="", response_key="followup_message", messages=["X"],
        )
        assert r2 == {"followup_message": "X"}


@pytest.mark.integration
class TestAdaptiveSkip:
    """Suppress when the nudge's ceremony tool has already been invoked."""

    def _seed_hook_log(self, tmp_path: Path, tool_name: str) -> None:
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "ts": ts, "level": "info", "component": "cursor-hook",
            "event": "preToolUse", "msg": f"preToolUse tool=MCP:{tool_name}",
        }
        (log_dir / "cursor-hooks.jsonl").write_text(json.dumps(entry) + "\n")

    def test_recent_ceremony_tool_suppresses_nudge(self, tmp_path: Path) -> None:
        """trw_deliver invoked in the last 30 minutes → stop nudge suppressed."""
        self._seed_hook_log(tmp_path, "trw_deliver")

        result = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c"},
            event_name="stop", cooldown=3600, adaptive_tool="trw_deliver",
            response_key="followup_message", messages=["X"],
        )
        assert result == {}

    def test_unrelated_tool_in_log_does_not_suppress(self, tmp_path: Path) -> None:
        """Only the named ceremony tool triggers adaptive skip; others don't."""
        self._seed_hook_log(tmp_path, "trw_learn")  # not the stop-nudge's target

        result = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c"},
            event_name="stop", cooldown=3600, adaptive_tool="trw_deliver",
            response_key="followup_message", messages=["X"],
        )
        assert result == {"followup_message": "X"}

    def test_old_ceremony_tool_does_not_suppress(self, tmp_path: Path) -> None:
        """If the tool was invoked > 30 min ago, don't treat as recent."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)
        entry = {
            "ts": old.strftime("%Y-%m-%dT%H:%M:%SZ"), "level": "info",
            "component": "cursor-hook", "event": "preToolUse",
            "msg": "preToolUse tool=MCP:trw_deliver",
        }
        (log_dir / "cursor-hooks.jsonl").write_text(json.dumps(entry) + "\n")

        result = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c"},
            event_name="stop", cooldown=3600, adaptive_tool="trw_deliver",
            response_key="followup_message", messages=["X"],
        )
        assert result == {"followup_message": "X"}

    def test_empty_adaptive_tool_skips_check(self, tmp_path: Path) -> None:
        """adaptive_tool='' disables the adaptive skip check entirely."""
        # Seed a log that would trigger the skip IF the check ran
        self._seed_hook_log(tmp_path, "trw_deliver")

        result = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key="followup_message", messages=["X"],
        )
        assert result == {"followup_message": "X"}


@pytest.mark.integration
class TestMessageRotation:
    """Stable per-conversation selection from a curated set."""

    def test_same_conversation_picks_same_message(self, tmp_path: Path) -> None:
        """Hashing conversation_id → deterministic message per session."""
        messages = ["A", "B", "C"]
        picks: list[str] = []
        for _ in range(3):
            # Need to run different conversations since state is shared
            pass
        # Use different conversations each time, read the picked message
        r1 = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "same-id"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key="followup_message", messages=messages,
        )
        # Same conversation → cooldown kicks in. Clear state and re-run.
        state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
        state_file.unlink()
        r2 = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "same-id"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key="followup_message", messages=messages,
        )
        assert r1 == r2

    def test_different_conversations_rotate_through_messages(
        self, tmp_path: Path
    ) -> None:
        """Many distinct conversations → distribution across the message set."""
        messages = ["A", "B", "C", "D"]
        picks: list[str] = []
        for i in range(20):
            # Use fresh tmp state per call to avoid cross-conversation dedup side effects
            state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
            if state_file.exists():
                state_file.unlink()
            r = _run_gate(
                tmp_path=tmp_path, payload={"conversation_id": f"convo-{i}"},
                event_name="stop", cooldown=3600, adaptive_tool="",
                response_key="followup_message", messages=messages,
            )
            picks.append(r["followup_message"])
        # With 20 distinct conversations and 4 messages, at least 2 distinct
        # messages should be picked. (Deterministic hash, but avoids asserting
        # a specific distribution.)
        assert len(set(picks)) >= 2


@pytest.mark.integration
class TestResponseKey:
    """The response_key argument controls the emitted JSON field."""

    @pytest.mark.parametrize(
        "key",
        ["followup_message", "additional_context", "user_message", "agent_message"],
    )
    def test_response_key_controls_output_field(
        self, tmp_path: Path, key: str
    ) -> None:
        """Each supported response key appears as the top-level JSON field."""
        result = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": f"c-{key}"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key=key, messages=["TEST"],
        )
        assert result == {key: "TEST"}


@pytest.mark.integration
class TestGenerationIdDedupForPreCompact:
    """preCompact uses generation_id (not conversation_id) for dedup."""

    def test_same_generation_id_dedups(self, tmp_path: Path) -> None:
        """Same generation_id → second preCompact call suppressed."""
        payload = {"conversation_id": "c", "generation_id": "gen-1"}
        r1 = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="preCompact",
            cooldown=300, adaptive_tool="", response_key="user_message",
            messages=["COMPACT"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path, payload=payload, event_name="preCompact",
            cooldown=300, adaptive_tool="", response_key="user_message",
            messages=["COMPACT"],
        )
        assert r1 == {"user_message": "COMPACT"}
        assert r2 == {}

    def test_different_generation_id_both_emit(self, tmp_path: Path) -> None:
        """Different generation_ids (distinct compaction events) both emit."""
        r1 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "gen-1"},
            event_name="preCompact", cooldown=300, adaptive_tool="",
            response_key="user_message", messages=["COMPACT"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "gen-2"},
            event_name="preCompact", cooldown=300, adaptive_tool="",
            response_key="user_message", messages=["COMPACT"],
        )
        assert r1 == {"user_message": "COMPACT"}
        assert r2 == {"user_message": "COMPACT"}


@pytest.mark.integration
class TestFailOpen:
    """Malformed inputs / missing files do not crash — gate returns {} silently."""

    def test_empty_stdin_returns_empty(self, tmp_path: Path) -> None:
        """Cursor may invoke hooks with empty stdin — don't crash."""
        env = {"CURSOR_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
        proc = subprocess.run(
            [sys.executable, str(_GATE_SCRIPT), "stop", "3600", "", "followup_message", '["X"]'],
            input="",
            capture_output=True, text=True, env=env, check=True, timeout=10,
        )
        # Empty conversation_id defaults to "default"; gate still emits
        assert json.loads(proc.stdout.strip())["followup_message"] == "X"

    def test_malformed_json_stdin_returns_default(self, tmp_path: Path) -> None:
        """Non-JSON stdin → payload defaults to empty dict, gate still functional."""
        env = {"CURSOR_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
        proc = subprocess.run(
            [sys.executable, str(_GATE_SCRIPT), "stop", "3600", "", "followup_message", '["X"]'],
            input="not json at all",
            capture_output=True, text=True, env=env, check=True, timeout=10,
        )
        # Still emits (empty conversation_id → "default" → no prior entry)
        assert proc.returncode == 0
        assert "followup_message" in json.loads(proc.stdout.strip())

    def test_empty_messages_list_returns_empty(self, tmp_path: Path) -> None:
        """No messages provided → gate emits {} instead of a null/empty message."""
        result = _run_gate(
            tmp_path=tmp_path, payload={"conversation_id": "c"},
            event_name="stop", cooldown=3600, adaptive_tool="",
            response_key="followup_message", messages=[],
        )
        assert result == {}


@pytest.mark.integration
class TestEndToEndBashHooks:
    """Exercise the actual bash hook scripts as subprocess — covers the
    bash→python pipeline + logging + temp-file handling."""

    def test_stop_hook_first_fire_emits(self, tmp_path: Path) -> None:
        result = _run_hook(
            _STOP_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c1"},
        )
        assert "followup_message" in result
        assert "trw_deliver" in result["followup_message"]

    def test_stop_hook_second_fire_suppresses(self, tmp_path: Path) -> None:
        _run_hook(_STOP_SCRIPT, tmp_path=tmp_path, payload={"conversation_id": "c1"})
        r2 = _run_hook(_STOP_SCRIPT, tmp_path=tmp_path, payload={"conversation_id": "c1"})
        assert r2 == {}

    def test_stop_hook_logs_to_cursor_hooks_jsonl(self, tmp_path: Path) -> None:
        """Observability: every fire writes a log line regardless of suppression."""
        _run_hook(_STOP_SCRIPT, tmp_path=tmp_path, payload={"conversation_id": "c1"})
        _run_hook(_STOP_SCRIPT, tmp_path=tmp_path, payload={"conversation_id": "c1"})

        log_file = tmp_path / ".trw" / "logs" / "cursor-hooks.jsonl"
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        stop_entries = [
            json.loads(l) for l in lines
            if json.loads(l).get("event") == "stop"
        ]
        assert len(stop_entries) == 2  # both fires logged

    def test_session_start_hook_respects_24h_cooldown(self, tmp_path: Path) -> None:
        """session-start cooldown is 24h — two fires in same convo → one emit."""
        r1 = _run_hook(
            _SESSION_START_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c-ss"},
        )
        r2 = _run_hook(
            _SESSION_START_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c-ss"},
        )
        assert "additional_context" in r1
        assert r2 == {}

    def test_pre_compact_hook_dedup_per_generation(self, tmp_path: Path) -> None:
        """pre-compact dedup keyed on generation_id, not conversation_id."""
        r1 = _run_hook(
            _PRE_COMPACT_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "g1"},
        )
        r2 = _run_hook(
            _PRE_COMPACT_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "g1"},
        )
        r3 = _run_hook(
            _PRE_COMPACT_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "g2"},
        )
        assert "user_message" in r1
        assert r2 == {}
        assert "user_message" in r3  # different generation → re-emit

    def test_stop_hook_adaptive_skip_when_deliver_logged(
        self, tmp_path: Path
    ) -> None:
        """Seed cursor-hooks.jsonl with a recent trw_deliver call → suppress."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (log_dir / "cursor-hooks.jsonl").write_text(
            json.dumps({
                "ts": ts, "level": "info", "component": "cursor-hook",
                "event": "preToolUse", "msg": "preToolUse tool=MCP:trw_deliver",
            }) + "\n"
        )

        result = _run_hook(
            _STOP_SCRIPT, tmp_path=tmp_path,
            payload={"conversation_id": "c-adapt"},
        )
        assert result == {}
