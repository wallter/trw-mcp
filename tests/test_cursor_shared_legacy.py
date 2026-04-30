"""Tests for shared Cursor legacy bootstrap compatibility helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


@pytest.mark.integration
def test_generate_cursor_hooks_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_hooks (legacy) creates .cursor/hooks.json on first call."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hooks

    result = generate_cursor_hooks(tmp_path)
    hooks_file = tmp_path / ".cursor" / "hooks.json"

    assert hooks_file.is_file()
    data = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert "hooks" in data
    assert isinstance(data["hooks"], list)
    assert len(data["hooks"]) > 0
    assert ".cursor/hooks.json" in result.get("created", [])


@pytest.mark.integration
def test_generate_cursor_hooks_smart_merge_preserves_user(tmp_path: Path) -> None:
    """generate_cursor_hooks smart-merges without losing user hooks on second run."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hooks

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    user_hooks_data: dict[str, Any] = {
        "hooks": [
            {
                "event": "stop",
                "command": "echo user-stop",
                "description": "User custom stop hook",
            }
        ]
    }
    (cursor_dir / "hooks.json").write_text(json.dumps(user_hooks_data), encoding="utf-8")

    generate_cursor_hooks(tmp_path)

    data = json.loads((cursor_dir / "hooks.json").read_text(encoding="utf-8"))
    commands = [h.get("command", "") for h in data["hooks"]]
    assert any("user-stop" in cmd for cmd in commands), (
        "User hook was lost during generate_cursor_hooks smart merge"
    )


@pytest.mark.integration
def test_generate_cursor_hooks_malformed_json_overwrites(tmp_path: Path) -> None:
    """generate_cursor_hooks overwrites malformed hooks.json with fresh content."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hooks

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "hooks.json").write_text("{{{invalid json", encoding="utf-8")

    generate_cursor_hooks(tmp_path)

    data = json.loads((cursor_dir / "hooks.json").read_text(encoding="utf-8"))
    assert "hooks" in data
    assert isinstance(data["hooks"], list)


@pytest.mark.unit
def test_get_trw_mcp_entry_cursor_uses_binary_when_on_path() -> None:
    """_get_trw_mcp_entry_cursor returns command='trw-mcp' when binary on PATH."""
    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor

    with patch("trw_mcp.bootstrap._cursor.shutil.which", return_value="/usr/local/bin/trw-mcp"):
        entry = _get_trw_mcp_entry_cursor()

    assert entry["command"] == "trw-mcp"
    assert "--debug" in entry.get("args", [])


@pytest.mark.unit
def test_get_trw_mcp_entry_cursor_falls_back_to_python_module() -> None:
    """_get_trw_mcp_entry_cursor falls back to sys.executable when binary absent."""
    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor

    with patch("trw_mcp.bootstrap._cursor.shutil.which", return_value=None):
        entry = _get_trw_mcp_entry_cursor()

    assert isinstance(entry["command"], list)
    assert entry["command"][0] == sys.executable
    assert "-m" in entry["command"]


@pytest.mark.unit
def test_hook_handler_entry_typeddict_exported() -> None:
    """HookHandlerEntry and CursorHooksV1Config are importable from _cursor."""
    from trw_mcp.bootstrap._cursor import CursorHooksV1Config, HookHandlerEntry

    handler: HookHandlerEntry = {"command": "trw-stop.sh", "type": "command", "timeout": 5}
    config: CursorHooksV1Config = {
        "version": 1,
        "hooks": {"stop": [handler]},
    }
    assert config["version"] == 1
    assert "stop" in config["hooks"]
