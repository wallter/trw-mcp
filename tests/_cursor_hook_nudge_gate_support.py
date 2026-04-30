"""Shared subprocess helpers for split cursor hook nudge-gate tests."""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "cursor"
_GATE_SCRIPT = _HOOKS_DIR / "_nudge_gate.py"
_STOP_SCRIPT = _HOOKS_DIR / "trw-stop.sh"
_SESSION_START_SCRIPT = _HOOKS_DIR / "trw-session-start.sh"
_PRE_COMPACT_SCRIPT = _HOOKS_DIR / "trw-pre-compact.sh"


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


def _seed_hook_log(tmp_path: Path, tool_name: str, *, ts: _dt.datetime | None = None) -> None:
    """Write a preToolUse hook-log entry for the given ceremony tool."""
    log_dir = tmp_path / ".trw" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    entry_ts = ts or _dt.datetime.now(_dt.timezone.utc)
    entry = {
        "ts": entry_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": "info",
        "component": "cursor-hook",
        "event": "preToolUse",
        "msg": f"preToolUse tool=MCP:{tool_name}",
    }
    (log_dir / "cursor-hooks.jsonl").write_text(json.dumps(entry) + "\n")
