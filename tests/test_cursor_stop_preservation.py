"""CORE269: installed Cursor Stop guidance preserves work without demanding delivery."""

import hashlib
import json
from pathlib import Path

import pytest

from tests._cursor_hook_nudge_gate_support import _STOP_SCRIPT, _run_hook
from tests._layout import requires_monorepo
from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks
from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_hooks


def _conversation_for_rotation(index: int) -> str:
    for number in range(100):
        value = f"preservation-{number}"
        if int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) % 3 == index:
            return value
    raise AssertionError("No conversation found for rotation")


@pytest.mark.parametrize("installer", [generate_cursor_cli_hooks, generate_cursor_ide_hooks])
def test_installed_stop_rotations_preserve_without_unconditional_delivery(tmp_path, installer):
    installer(tmp_path)
    hooks = json.loads((tmp_path / ".cursor/hooks.json").read_text())
    assert any(row["command"] == ".cursor/hooks/trw-stop.sh" for row in hooks["hooks"]["stop"])
    installed = tmp_path / ".cursor/hooks/trw-stop.sh"
    assert installed.read_bytes() == _STOP_SCRIPT.read_bytes()
    messages = []
    for index in range(3):
        response = _run_hook(
            installed, tmp_path=tmp_path, payload={"conversation_id": _conversation_for_rotation(index)}
        )
        message = response["followup_message"]
        messages.append(message)
        assert "If you have material unfinished work" in message
        assert "trw_checkpoint" in message and "durable native handoff" in message
        assert "next-read pointer" in message
        assert "nothing material" in message and "do not invent" in message
        assert "completed work" in message and "existing gates" in message
        assert "Before ending, call" not in message
    assert len(set(messages)) == 3


@pytest.mark.parametrize("installer", [generate_cursor_cli_hooks, generate_cursor_ide_hooks])
def test_stop_managed_update_and_user_edit_preservation(tmp_path, installer):
    installer(tmp_path)
    installed = tmp_path / ".cursor/hooks/trw-stop.sh"
    legacy = b"#!/usr/bin/env bash\necho 'old managed reminder'\n"
    installed.write_bytes(legacy)
    baseline = {"trw-stop.sh": hashlib.sha256(legacy).hexdigest()}
    result = installer(tmp_path, manifest_hashes=baseline)
    assert installed.read_bytes() == _STOP_SCRIPT.read_bytes()
    assert ".cursor/hooks/trw-stop.sh" in result["updated"]
    baseline = {"trw-stop.sh": hashlib.sha256(installed.read_bytes()).hexdigest()}
    edited = installed.read_bytes() + b"\n# User-owned annotation\n"
    installed.write_bytes(edited)
    result = installer(tmp_path, manifest_hashes=baseline)
    assert installed.read_bytes() == edited
    assert ".cursor/hooks/trw-stop.sh" in result["preserved"]


@requires_monorepo
def test_repo_stop_mirrors_bundle():
    root = Path(__file__).resolve().parents[2]
    assert (root / ".cursor/hooks/trw-stop.sh").read_bytes() == _STOP_SCRIPT.read_bytes()
