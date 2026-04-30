"""End-to-end bash hook tests for the shared Cursor hook nudge gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._cursor_hook_nudge_gate_support import (
    _PRE_COMPACT_SCRIPT,
    _SESSION_START_SCRIPT,
    _STOP_SCRIPT,
    _run_hook,
    _seed_hook_log,
)


@pytest.mark.integration
class TestEndToEndBashHooks:
    """Exercise the actual bash hook scripts as subprocess."""

    def test_stop_hook_first_fire_emits(self, tmp_path: Path) -> None:
        result = _run_hook(
            _STOP_SCRIPT,
            tmp_path=tmp_path,
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
        lines = [line for line in log_file.read_text().splitlines() if line.strip()]
        stop_entries = [json.loads(line) for line in lines if json.loads(line).get("event") == "stop"]
        assert len(stop_entries) == 2

    def test_session_start_hook_respects_24h_cooldown(self, tmp_path: Path) -> None:
        """session-start cooldown is 24h — two fires in same convo → one emit."""
        r1 = _run_hook(
            _SESSION_START_SCRIPT,
            tmp_path=tmp_path,
            payload={"conversation_id": "c-ss"},
        )
        r2 = _run_hook(
            _SESSION_START_SCRIPT,
            tmp_path=tmp_path,
            payload={"conversation_id": "c-ss"},
        )
        assert "additional_context" in r1
        assert r2 == {}

    def test_pre_compact_hook_dedup_per_generation(self, tmp_path: Path) -> None:
        """pre-compact dedup keyed on generation_id, not conversation_id."""
        r1 = _run_hook(
            _PRE_COMPACT_SCRIPT,
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "g1"},
        )
        r2 = _run_hook(
            _PRE_COMPACT_SCRIPT,
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "g1"},
        )
        r3 = _run_hook(
            _PRE_COMPACT_SCRIPT,
            tmp_path=tmp_path,
            payload={"conversation_id": "c", "generation_id": "g2"},
        )
        assert "user_message" in r1
        assert r2 == {}
        assert "user_message" in r3

    def test_stop_hook_adaptive_skip_when_deliver_logged(self, tmp_path: Path) -> None:
        """Seed cursor-hooks.jsonl with a recent trw_deliver call → suppress."""
        _seed_hook_log(tmp_path, "trw_deliver")

        result = _run_hook(
            _STOP_SCRIPT,
            tmp_path=tmp_path,
            payload={"conversation_id": "c-adapt"},
        )
        assert result == {}
