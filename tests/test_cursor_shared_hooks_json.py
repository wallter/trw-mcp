"""Tests for shared Cursor hook config and JSON smart-merge helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _make_hooks_json(events: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a hooks.json structure for test fixtures."""
    return {"version": 1, "hooks": events}


@pytest.mark.unit
def test_build_cursor_hook_config_valid_structure() -> None:
    """build_cursor_hook_config returns {version:1, hooks:events_map}."""
    from trw_mcp.bootstrap._cursor import build_cursor_hook_config

    events_map = {
        "stop": [{"command": "bash trw-stop.sh", "description": "TRW stop"}],
    }
    result = build_cursor_hook_config(events_map)

    assert result["version"] == 1
    assert result["hooks"] is events_map


@pytest.mark.unit
def test_build_cursor_hook_config_raises_on_missing_command() -> None:
    """build_cursor_hook_config raises ValueError if any handler lacks 'command'."""
    from trw_mcp.bootstrap._cursor import build_cursor_hook_config

    events_map = {
        "stop": [{"description": "TRW stop"}],
    }
    with pytest.raises(ValueError, match=r"missing.*required key 'command'"):
        build_cursor_hook_config(events_map)


@pytest.mark.unit
def test_build_cursor_hook_config_empty_events_map() -> None:
    """build_cursor_hook_config accepts an empty events_map."""
    from trw_mcp.bootstrap._cursor import build_cursor_hook_config

    result = build_cursor_hook_config({})
    assert result == {"version": 1, "hooks": {}}


@pytest.mark.integration
def test_smart_merge_cursor_json_fresh_write(tmp_path: Path) -> None:
    """smart_merge_cursor_json creates the file when absent."""
    from trw_mcp.bootstrap._cursor import smart_merge_cursor_json

    target = tmp_path / ".cursor" / "hooks.json"
    trw_entries = _make_hooks_json({"stop": [{"command": "trw-stop.sh"}]})

    result = smart_merge_cursor_json(target, trw_entries, "trw-")
    data = json.loads(target.read_text(encoding="utf-8"))

    assert data["version"] == 1
    assert "stop" in data["hooks"]
    assert str(target) in result.get("created", [])


@pytest.mark.integration
def test_smart_merge_cursor_json_preserves_user_hooks(tmp_path: Path) -> None:
    """smart_merge_cursor_json preserves user hooks outside the TRW prefix."""
    from trw_mcp.bootstrap._cursor import smart_merge_cursor_json

    target = tmp_path / ".cursor" / "hooks.json"
    target.parent.mkdir(parents=True)
    existing = _make_hooks_json(
        {
            "stop": [{"command": "user-custom-stop.sh", "description": "my stop hook"}],
        }
    )
    target.write_text(json.dumps(existing), encoding="utf-8")

    trw_entries = _make_hooks_json({"stop": [{"command": "trw-stop.sh", "description": "TRW"}]})
    smart_merge_cursor_json(target, trw_entries, "trw-")

    data = json.loads(target.read_text(encoding="utf-8"))
    stop_handlers = data["hooks"]["stop"]
    commands = [h["command"] for h in stop_handlers]
    assert "user-custom-stop.sh" in commands
    assert "trw-stop.sh" in commands


@pytest.mark.integration
def test_smart_merge_cursor_json_replaces_prior_trw_entries(tmp_path: Path) -> None:
    """smart_merge_cursor_json removes stale TRW entries before inserting new ones."""
    from trw_mcp.bootstrap._cursor import smart_merge_cursor_json

    target = tmp_path / ".cursor" / "hooks.json"
    target.parent.mkdir(parents=True)
    existing = _make_hooks_json(
        {
            "stop": [{"command": "trw-old-stop.sh", "description": "old TRW"}],
        }
    )
    target.write_text(json.dumps(existing), encoding="utf-8")

    trw_entries = _make_hooks_json({"stop": [{"command": "trw-new-stop.sh", "description": "new TRW"}]})
    smart_merge_cursor_json(target, trw_entries, "trw-")

    data = json.loads(target.read_text(encoding="utf-8"))
    stop_commands = [h["command"] for h in data["hooks"]["stop"]]
    assert "trw-old-stop.sh" not in stop_commands
    assert "trw-new-stop.sh" in stop_commands


@pytest.mark.integration
def test_smart_merge_cursor_json_malformed_overwrites(tmp_path: Path) -> None:
    """smart_merge_cursor_json overwrites malformed JSON with TRW entries + warning."""
    from trw_mcp.bootstrap._cursor import smart_merge_cursor_json

    target = tmp_path / ".cursor" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text("{{ not valid json !!!", encoding="utf-8")

    trw_entries = _make_hooks_json({"stop": [{"command": "trw-stop.sh"}]})
    result = smart_merge_cursor_json(target, trw_entries, "trw-")

    data = json.loads(target.read_text(encoding="utf-8"))
    assert "hooks" in data
    assert str(target) in result.get("updated", [])


@pytest.mark.integration
def test_smart_merge_cursor_json_creates_parent_dirs(tmp_path: Path) -> None:
    """smart_merge_cursor_json creates missing parent directories."""
    from trw_mcp.bootstrap._cursor import smart_merge_cursor_json

    target = tmp_path / ".cursor" / "deep" / "nested" / "config.json"
    trw_entries: dict[str, Any] = {"key": "value"}

    smart_merge_cursor_json(target, trw_entries, "")

    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["key"] == "value"
