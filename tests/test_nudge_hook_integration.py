"""PRD-CORE-146 Wave 2C FR12 — cursor hook nudge-gate dispatch integration.

End-to-end exercise of ``trw-mcp/src/trw_mcp/data/hooks/cursor/_nudge_gate.py``
for the ``cursor-ide`` and ``cursor-cli`` profiles. Both profiles default to
``nudge_enabled=True``, so the gate should emit a rotated reminder message
under the pinned ``response_key`` when cooldown and adaptive-skip gates do
not suppress.

Driven via subprocess + stdin/stdout so the full script contract (argv
parsing, payload parsing, cooldown state, output shape) is exercised.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_GATE_SCRIPT: Path = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "cursor" / "_nudge_gate.py"


def _invoke_gate(
    *,
    tmp_path: Path,
    payload: dict[str, object],
    event_name: str,
    cooldown: int,
    adaptive_tool: str,
    response_key: str,
    messages: list[str],
) -> dict[str, object]:
    """Spawn ``_nudge_gate.py`` as a subprocess and return parsed stdout JSON."""
    env = {
        "CURSOR_PROJECT_DIR": str(tmp_path),
        "PATH": "/usr/bin:/bin",
    }
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
    stdout = proc.stdout.strip() or "{}"
    return json.loads(stdout)


@pytest.mark.parametrize("profile_id", ["cursor-ide", "cursor-cli"])
def test_cursor_hook_nudge_gate_dispatch(profile_id: str, tmp_path: Path) -> None:
    """FR12: a fresh-session Cursor hook invocation produces a nudge message
    keyed under the pinned ``response_key`` (e.g. ``followup_message``) for
    both cursor-ide and cursor-cli profiles.

    The hook script itself is profile-agnostic (the bash wrappers curate
    the messages per profile). This test verifies:

      1. The script exits 0 on valid input.
      2. stdout is valid JSON.
      3. When cooldown has not triggered and messages are non-empty, the
         output carries the rotated message under ``response_key``.
      4. A subsequent call within the cooldown window is suppressed
         (stdout == ``{}``) — confirming the anti-fatigue gate.

    Both profiles share the same gate path; the parametrization asserts the
    profile-agnostic contract holds for each.
    """
    assert _GATE_SCRIPT.is_file(), f"cursor nudge gate missing: {_GATE_SCRIPT}"

    # Unique conversation per profile so state doesn't bleed between runs.
    payload: dict[str, object] = {
        "conversation_id": f"conv-{profile_id}-fr12",
        "hook_event_name": "stop",
    }
    messages = [
        "TRW: Before ending, call trw_deliver() to persist your discoveries.",
        "TRW: Wrap up with trw_deliver() so learnings survive the session.",
    ]

    # --- first call: should emit ---
    first = _invoke_gate(
        tmp_path=tmp_path,
        payload=payload,
        event_name="stop",
        cooldown=3600,
        adaptive_tool="trw_deliver",
        response_key="followup_message",
        messages=messages,
    )

    # Contract: the output is either the rotated message or {} (suppressed).
    # With no prior state file and no matching tool in the hook log, the
    # gate MUST emit.
    assert "followup_message" in first, f"{profile_id}: expected followup_message in hook output, got {first!r}"
    assert first["followup_message"] in messages

    # --- second call: within cooldown window => suppressed ---
    second = _invoke_gate(
        tmp_path=tmp_path,
        payload=payload,
        event_name="stop",
        cooldown=3600,
        adaptive_tool="trw_deliver",
        response_key="followup_message",
        messages=messages,
    )
    assert second == {}, f"{profile_id}: second call within cooldown should be suppressed, got {second!r}"

    # Confirm state file was written to the isolated CURSOR_PROJECT_DIR,
    # not to the real project — proves env isolation.
    state_file = tmp_path / ".trw" / "logs" / "cursor-nudge-state.jsonl"
    assert state_file.is_file(), f"{profile_id}: nudge-gate should persist emission state at {state_file}"


@pytest.mark.parametrize("profile_id", ["cursor-ide", "cursor-cli"])
def test_cursor_hook_nudge_gate_empty_messages_suppressed(profile_id: str, tmp_path: Path) -> None:
    """FR12 corollary: when messages is an empty list, the gate must print
    ``{}`` regardless of profile — nothing to rotate through.
    """
    payload = {"conversation_id": f"conv-{profile_id}-empty", "hook_event_name": "stop"}

    result = _invoke_gate(
        tmp_path=tmp_path,
        payload=payload,
        event_name="stop",
        cooldown=3600,
        adaptive_tool="",
        response_key="followup_message",
        messages=[],
    )
    assert result == {}, f"{profile_id}: empty messages must suppress emission, got {result!r}"
