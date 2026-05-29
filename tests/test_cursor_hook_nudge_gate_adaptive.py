"""Adaptive-skip tests for the shared Cursor hook nudge gate."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from tests._cursor_hook_nudge_gate_support import _run_gate, _seed_hook_log


@pytest.mark.integration
class TestAdaptiveSkip:
    """Suppress when the nudge's ceremony tool has already been invoked."""

    def test_recent_ceremony_tool_suppresses_nudge(self, tmp_path: Path) -> None:
        """trw_deliver invoked in the last 30 minutes → stop nudge suppressed."""
        _seed_hook_log(tmp_path, "trw_deliver")

        result = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="trw_deliver",
            response_key="followup_message",
            messages=["X"],
        )
        assert result == {}

    def test_unrelated_tool_in_log_does_not_suppress(self, tmp_path: Path) -> None:
        """Only the named ceremony tool triggers adaptive skip; others don't."""
        _seed_hook_log(tmp_path, "trw_learn")

        result = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="trw_deliver",
            response_key="followup_message",
            messages=["X"],
        )
        assert result == {"followup_message": "X"}

    def test_old_ceremony_tool_does_not_suppress(self, tmp_path: Path) -> None:
        """If the tool was invoked > 30 min ago, don't treat as recent."""
        old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)
        _seed_hook_log(tmp_path, "trw_deliver", ts=old)

        result = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="trw_deliver",
            response_key="followup_message",
            messages=["X"],
        )
        assert result == {"followup_message": "X"}

    def test_empty_adaptive_tool_skips_check(self, tmp_path: Path) -> None:
        """adaptive_tool='' disables the adaptive skip check entirely."""
        _seed_hook_log(tmp_path, "trw_deliver")

        result = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=["X"],
        )
        assert result == {"followup_message": "X"}
