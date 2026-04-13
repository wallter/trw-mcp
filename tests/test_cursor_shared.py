"""Tests for shared Cursor bootstrap helpers in _cursor.py.

Covers all seven named exports (PRD-CORE-136-FR02):
  generate_cursor_mcp_config
  generate_cursor_rules_mdc
  generate_cursor_rules (alias)
  generate_cursor_skills_mirror
  generate_cursor_hook_scripts
  build_cursor_hook_config
  smart_merge_cursor_json

Each helper tested with:
  - fresh-write (file absent)
  - smart-merge with user content preserved
  - malformed / missing-input fallback
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_hooks_json(events: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a hooks.json structure for test fixtures."""
    return {"version": 1, "hooks": events}


# ===========================================================================
# 1. generate_cursor_mcp_config
# ===========================================================================


@pytest.mark.integration
def test_cursor_mcp_config_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_mcp_config creates .cursor/mcp.json on first call."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    result = generate_cursor_mcp_config(tmp_path)
    mcp_file = tmp_path / ".cursor" / "mcp.json"

    assert mcp_file.is_file()
    data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "trw" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("created", [])


@pytest.mark.integration
def test_cursor_mcp_config_smart_merge_preserves_user_servers(tmp_path: Path) -> None:
    """generate_cursor_mcp_config preserves existing non-TRW server entries."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    mcp_file = cursor_dir / "mcp.json"
    mcp_file.write_text(
        json.dumps({"mcpServers": {"my-server": {"command": "my-server-bin"}}}),
        encoding="utf-8",
    )

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads(mcp_file.read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    assert "my-server" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_mcp_config_malformed_json_overwrites(tmp_path: Path) -> None:
    """generate_cursor_mcp_config overwrites malformed mcp.json with fresh content."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text("not valid {{{", encoding="utf-8")

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    # Malformed → updated (the file existed, was overwritten)
    assert ".cursor/mcp.json" in result.get("updated", [])


# ===========================================================================
# 2. generate_cursor_rules_mdc (new canonical name)
# ===========================================================================


@pytest.mark.integration
def test_cursor_rules_mdc_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_rules_mdc creates .cursor/rules/trw-ceremony.mdc on first call."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    result = generate_cursor_rules_mdc(tmp_path, "TRW ceremony content", client_id="cursor-ide")
    rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"

    assert rules_file.is_file()
    content = rules_file.read_text(encoding="utf-8")
    assert "alwaysApply: true" in content
    assert "TRW ceremony content" in content
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("created", [])


@pytest.mark.integration
def test_cursor_rules_mdc_updates_existing(tmp_path: Path) -> None:
    """generate_cursor_rules_mdc updates the file when it already exists."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    rules_dir = tmp_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "trw-ceremony.mdc").write_text("old content", encoding="utf-8")

    result = generate_cursor_rules_mdc(tmp_path, "new content")
    content = (rules_dir / "trw-ceremony.mdc").read_text(encoding="utf-8")

    assert "new content" in content
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_rules_mdc_client_id_cursor_cli(tmp_path: Path) -> None:
    """generate_cursor_rules_mdc accepts cursor-cli as client_id."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    result = generate_cursor_rules_mdc(tmp_path, "CLI content", client_id="cursor-cli")
    rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"

    assert rules_file.is_file()
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("created", [])


# ===========================================================================
# 3. generate_cursor_rules (backward-compat alias)
# ===========================================================================


@pytest.mark.integration
def test_cursor_rules_alias_delegates_to_mdc(tmp_path: Path) -> None:
    """generate_cursor_rules (alias) produces the same output as generate_cursor_rules_mdc."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules

    result = generate_cursor_rules(tmp_path, "alias content")
    rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"

    assert rules_file.is_file()
    assert "alias content" in rules_file.read_text(encoding="utf-8")
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("created", [])


# ===========================================================================
# 4. generate_cursor_skills_mirror
# ===========================================================================


@pytest.mark.integration
def test_cursor_skills_mirror_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror copies skill dirs to .cursor/skills/."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    # Create a fake source skill directory
    source_dir = tmp_path / "fake_skills"
    (source_dir / "trw-deliver").mkdir(parents=True)
    (source_dir / "trw-deliver" / "SKILL.md").write_text("# trw-deliver", encoding="utf-8")

    result = generate_cursor_skills_mirror(tmp_path, ["trw-deliver"], source_dir=source_dir)
    dest = tmp_path / ".cursor" / "skills" / "trw-deliver" / "SKILL.md"

    assert dest.is_file()
    assert ".cursor/skills/trw-deliver" in result.get("created", [])


@pytest.mark.integration
def test_cursor_skills_mirror_preserves_user_skills(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror does NOT remove user skills outside the list."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    # Create a user skill that should be preserved
    user_skill = tmp_path / ".cursor" / "skills" / "my-custom-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("my custom skill", encoding="utf-8")

    # Create a fake source skill
    source_dir = tmp_path / "fake_skills"
    (source_dir / "trw-deliver").mkdir(parents=True)
    (source_dir / "trw-deliver" / "SKILL.md").write_text("# trw-deliver", encoding="utf-8")

    generate_cursor_skills_mirror(tmp_path, ["trw-deliver"], source_dir=source_dir)

    # User skill must be preserved
    assert (user_skill / "SKILL.md").is_file()
    assert (user_skill / "SKILL.md").read_text() == "my custom skill"


@pytest.mark.integration
def test_cursor_skills_mirror_missing_source_warns_and_skips(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror skips missing source skills with a warning."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    source_dir = tmp_path / "empty_skills"
    source_dir.mkdir()

    # no-op — source skill doesn't exist
    result = generate_cursor_skills_mirror(tmp_path, ["nonexistent-skill"], source_dir=source_dir)

    assert result.get("created", []) == []
    assert not (tmp_path / ".cursor" / "skills" / "nonexistent-skill").exists()


# ===========================================================================
# 5. generate_cursor_hook_scripts
# ===========================================================================


@pytest.mark.integration
def test_cursor_hook_scripts_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts copies bundled scripts to .cursor/hooks/."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    # Seed a fake bundled hooks data dir
    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)
    (fake_data / "trw-session-start.sh").write_text("#!/usr/bin/env bash\necho hi", encoding="utf-8")

    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["trw-session-start.sh"])

    dest = tmp_path / ".cursor" / "hooks" / "trw-session-start.sh"
    assert dest.is_file()
    # Must be executable
    mode = stat.S_IMODE(os.stat(str(dest)).st_mode)
    assert mode & 0o111  # at least one execute bit set
    assert ".cursor/hooks/trw-session-start.sh" in result.get("created", [])


@pytest.mark.integration
def test_cursor_hook_scripts_idempotent_without_force(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts preserves existing scripts when force=False."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)
    (fake_data / "trw-stop.sh").write_text("#!/usr/bin/env bash\necho stop", encoding="utf-8")

    # First write
    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"])
    # Second call without force — should preserve
    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"])

    assert ".cursor/hooks/trw-stop.sh" in result.get("preserved", [])


@pytest.mark.integration
def test_cursor_hook_scripts_missing_bundled_script_warns(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts skips missing bundled scripts with a warning."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)  # empty directory

    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["nonexistent.sh"])

    assert result.get("created", []) == []
    assert not (tmp_path / ".cursor" / "hooks" / "nonexistent.sh").exists()


@pytest.mark.integration
def test_cursor_hook_scripts_force_overwrites_existing(tmp_path: Path) -> None:
    """generate_cursor_hook_scripts with force=True overwrites existing scripts and reports updated."""
    from trw_mcp.bootstrap._cursor import generate_cursor_hook_scripts

    fake_data = tmp_path / "fake_data" / "hooks" / "cursor"
    fake_data.mkdir(parents=True)
    (fake_data / "trw-stop.sh").write_text("#!/usr/bin/env bash\necho v2", encoding="utf-8")

    # First write
    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"])

    # Second call with force=True — should overwrite and report "updated"
    with patch("trw_mcp.bootstrap._cursor._CURSOR_HOOKS_DATA_DIR", fake_data):
        result = generate_cursor_hook_scripts(tmp_path, ["trw-stop.sh"], force=True)

    assert ".cursor/hooks/trw-stop.sh" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_skills_mirror_force_removes_existing(tmp_path: Path) -> None:
    """generate_cursor_skills_mirror with force=True removes and re-copies skill dirs."""
    from trw_mcp.bootstrap._cursor import generate_cursor_skills_mirror

    # Seed the skill in both source and destination
    source_dir = tmp_path / "fake_skills"
    (source_dir / "trw-deliver").mkdir(parents=True)
    (source_dir / "trw-deliver" / "SKILL.md").write_text("# v2", encoding="utf-8")

    dest_skill = tmp_path / ".cursor" / "skills" / "trw-deliver"
    dest_skill.mkdir(parents=True)
    (dest_skill / "SKILL.md").write_text("# v1 (stale)", encoding="utf-8")
    (dest_skill / "stale-extra.md").write_text("stale", encoding="utf-8")

    # force=True should clean and re-copy
    result = generate_cursor_skills_mirror(tmp_path, ["trw-deliver"], source_dir=source_dir, force=True)

    updated = result.get("created", []) + result.get("updated", [])
    assert ".cursor/skills/trw-deliver" in updated
    # Stale file should be gone (dir was rmtree'd before copy)
    assert not (dest_skill / "stale-extra.md").exists()


# ===========================================================================
# 6. build_cursor_hook_config
# ===========================================================================


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
        "stop": [{"description": "TRW stop"}],  # missing 'command'
    }
    with pytest.raises(ValueError, match="missing.*required key 'command'"):
        build_cursor_hook_config(events_map)


@pytest.mark.unit
def test_build_cursor_hook_config_empty_events_map() -> None:
    """build_cursor_hook_config accepts an empty events_map."""
    from trw_mcp.bootstrap._cursor import build_cursor_hook_config

    result = build_cursor_hook_config({})
    assert result == {"version": 1, "hooks": {}}


# ===========================================================================
# 7. smart_merge_cursor_json
# ===========================================================================


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
    existing = _make_hooks_json({
        "stop": [{"command": "user-custom-stop.sh", "description": "my stop hook"}],
    })
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
    existing = _make_hooks_json({
        "stop": [{"command": "trw-old-stop.sh", "description": "old TRW"}],
    })
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


# ===========================================================================
# 8. generate_cursor_hooks (legacy backward-compat function)
# ===========================================================================


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
    # User hook should survive the merge
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


# ===========================================================================
# 9. _get_trw_mcp_entry_cursor (entry detection helper)
# ===========================================================================


@pytest.mark.unit
def test_get_trw_mcp_entry_cursor_uses_binary_when_on_path() -> None:
    """_get_trw_mcp_entry_cursor returns command='trw-mcp' when binary on PATH."""
    from unittest.mock import patch

    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor

    with patch("trw_mcp.bootstrap._cursor.shutil.which", return_value="/usr/local/bin/trw-mcp"):
        entry = _get_trw_mcp_entry_cursor()

    assert entry["command"] == "trw-mcp"
    assert "--debug" in entry.get("args", [])


@pytest.mark.unit
def test_get_trw_mcp_entry_cursor_falls_back_to_python_module() -> None:
    """_get_trw_mcp_entry_cursor falls back to sys.executable when binary absent."""
    import sys
    from unittest.mock import patch

    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor

    with patch("trw_mcp.bootstrap._cursor.shutil.which", return_value=None):
        entry = _get_trw_mcp_entry_cursor()

    # command should be a list [sys.executable, "-m", "trw_mcp.server"]
    assert isinstance(entry["command"], list)
    assert entry["command"][0] == sys.executable
    assert "-m" in entry["command"]


# ===========================================================================
# 10. HookHandlerEntry / CursorHooksV1Config TypedDicts
# ===========================================================================


@pytest.mark.unit
def test_hook_handler_entry_typeddict_exported() -> None:
    """HookHandlerEntry and CursorHooksV1Config are importable from _cursor."""
    from trw_mcp.bootstrap._cursor import CursorHooksV1Config, HookHandlerEntry

    # Verify they can be used as expected (runtime dicts, not classes to instantiate)
    handler: HookHandlerEntry = {"command": "trw-stop.sh", "type": "command", "timeout": 5}
    config: CursorHooksV1Config = {
        "version": 1,
        "hooks": {"stop": [handler]},
    }
    assert config["version"] == 1
    assert "stop" in config["hooks"]
