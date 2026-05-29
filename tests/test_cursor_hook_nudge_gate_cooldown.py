"""Cooldown and dedup tests for the shared Cursor hook nudge gate."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from tests._cursor_hook_nudge_gate_support import _run_gate


@pytest.mark.integration
class TestCooldownDedup:
    """Anti-fatigue: repeat emissions within the cooldown window are suppressed."""

    def test_second_call_within_cooldown_suppresses(self, tmp_path: Path) -> None:
        """Two calls with same (event, conversation) → first emits, second is {}."""
        payload = {"conversation_id": "convo-1"}
        first = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=["MSG-A"],
        )
        second = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=["MSG-A"],
        )

        assert first == {"followup_message": "MSG-A"}
        assert second == {}

    def test_different_conversation_ids_both_emit(self, tmp_path: Path) -> None:
        """Independent conversations each get one emission."""
        r1 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c-1"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c-2"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )
        assert r1 == {"followup_message": "X"}
        assert r2 == {"followup_message": "X"}

    def test_zero_cooldown_never_dedups(self, tmp_path: Path) -> None:
        """cooldown=0 → every call emits (disables dedup)."""
        payload = {"conversation_id": "c"}
        r1 = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="stop",
            cooldown=0,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="stop",
            cooldown=0,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )
        assert r1 == {"followup_message": "X"}
        assert r2 == {"followup_message": "X"}

    def test_expired_cooldown_re_emits(self, tmp_path: Path) -> None:
        """Backdating the state entry past the cooldown window → next call emits."""
        payload = {"conversation_id": "c"}
        _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="stop",
            cooldown=60,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )

        state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
        lines = state_file.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=7200)
        rec["ts"] = past.strftime("%Y-%m-%dT%H:%M:%SZ")
        state_file.write_text(json.dumps(rec) + "\n")

        r2 = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="stop",
            cooldown=60,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )
        assert r2 == {"followup_message": "X"}


@pytest.mark.integration
class TestGenerationIdDedupForPreCompact:
    """preCompact uses generation_id (not conversation_id) for dedup."""

    def test_same_generation_id_dedups(self, tmp_path: Path) -> None:
        """Same generation_id → second preCompact call suppressed."""
        payload = {"conversation_id": "c", "generation_id": "gen-1"}
        r1 = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="preCompact",
            cooldown=300,
            adaptive_tool="",
            response_key="user_message",
            messages=["COMPACT"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path,
            payload=payload,
            event_name="preCompact",
            cooldown=300,
            adaptive_tool="",
            response_key="user_message",
            messages=["COMPACT"],
        )
        assert r1 == {"user_message": "COMPACT"}
        assert r2 == {}

    def test_different_generation_id_both_emit(self, tmp_path: Path) -> None:
        """Different generation_ids (distinct compaction events) both emit."""
        r1 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "gen-1"},
            event_name="preCompact",
            cooldown=300,
            adaptive_tool="",
            response_key="user_message",
            messages=["COMPACT"],
        )
        r2 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "gen-2"},
            event_name="preCompact",
            cooldown=300,
            adaptive_tool="",
            response_key="user_message",
            messages=["COMPACT"],
        )
        assert r1 == {"user_message": "COMPACT"}
        assert r2 == {"user_message": "COMPACT"}
