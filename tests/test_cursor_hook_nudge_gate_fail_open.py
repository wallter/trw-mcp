"""Fail-open tests for the shared Cursor hook nudge gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._cursor_hook_nudge_gate_support import _GATE_SCRIPT, _run_gate


@pytest.mark.integration
class TestFailOpen:
    """Malformed inputs / missing files do not crash — gate returns {} silently."""

    def test_empty_stdin_returns_empty(self, tmp_path: Path) -> None:
        """Cursor may invoke hooks with empty stdin — don't crash."""
        env = {"CURSOR_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
        proc = subprocess.run(
            [sys.executable, str(_GATE_SCRIPT), "stop", "3600", "", "followup_message", '["X"]'],
            input="",
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=10,
        )
        assert json.loads(proc.stdout.strip())["followup_message"] == "X"

    def test_malformed_json_stdin_returns_default(self, tmp_path: Path) -> None:
        """Non-JSON stdin → payload defaults to empty dict, gate still functional."""
        env = {"CURSOR_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
        proc = subprocess.run(
            [sys.executable, str(_GATE_SCRIPT), "stop", "3600", "", "followup_message", '["X"]'],
            input="not json at all",
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=10,
        )
        assert proc.returncode == 0
        assert "followup_message" in json.loads(proc.stdout.strip())

    def test_empty_messages_list_returns_empty(self, tmp_path: Path) -> None:
        """No messages provided → gate emits {} instead of a null/empty message."""
        result = _run_gate(
            tmp_path=tmp_path,
            payload={"conversation_id": "c"},
            event_name="stop",
            cooldown=3600,
            adaptive_tool="",
            response_key="followup_message",
            messages=[],
        )
        assert result == {}
