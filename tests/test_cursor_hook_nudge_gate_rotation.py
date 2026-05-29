"""Rotation and response-key tests for the shared Cursor hook nudge gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._cursor_hook_nudge_gate_support import _run_gate


@pytest.mark.integration
class TestMessageRotation:
    """Stable per-conversation selection from a curated set."""

    def test_same_conversation_picks_same_message(self, tmp_path: Path) -> None:
        """Hashing conversation_id → deterministic message per session."""
        messages = ["A", "B", "C"]
        r1 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "same-id"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=messages,
        )
        state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
        state_file.unlink()
        r2 = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "same-id"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=messages,
        )
        assert r1 == r2

    def test_different_conversations_rotate_through_messages(self, tmp_path: Path) -> None:
        """Many distinct conversations → distribution across the message set."""
        messages = ["A", "B", "C", "D"]
        picks: list[str] = []
        for i in range(20):
            state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
            if state_file.exists():
                state_file.unlink()
            result = _run_gate(
                tmp_path=tmp_path,
                payload={"conversation_id": f"convo-{i}"},
                event_name="stop",
                cooldown=3600,
                adaptive_tool="",
                response_key="followup_message",
                messages=messages,
            )
            picks.append(result["followup_message"])
        assert len(set(picks)) >= 2


@pytest.mark.integration
class TestResponseKey:
    """The response_key argument controls the emitted JSON field."""

    @pytest.mark.parametrize(
        "key",
        ["followup_message", "additional_context", "user_message", "agent_message"],
    )
    def test_response_key_controls_output_field(self, tmp_path: Path, key: str) -> None:
        """Each supported response key appears as the top-level JSON field."""
        result = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": f"c-{key}"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key=key,
            messages=["TEST"],
        )
        assert result == {key: "TEST"}
