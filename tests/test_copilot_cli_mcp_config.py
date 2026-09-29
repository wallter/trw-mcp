"""The copilot install configures trw for the Copilot CLI, not only for VS Code.

The Copilot CLI reads project MCP servers from ``.mcp.json`` and ``.github/mcp.json`` and
never from ``.vscode/mcp.json``, the only file the copilot install used to write, so CLI
sessions had no trw tools (DoD-5 COPILOT-CLI-NO-MCP). These tests render a real
``init_project(ide="copilot")`` into ``tmp_path`` and read the files a user gets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _install(tmp_path: Path) -> dict[str, object]:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir(exist_ok=True)
    result = init_project(tmp_path, ide="copilot")
    assert not result["errors"], result["errors"]
    return result


def _cli_config(tmp_path: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads((tmp_path / ".github" / "mcp.json").read_text(encoding="utf-8"))
    return loaded


def test_copilot_install_writes_the_cli_mcp_config(tmp_path: Path) -> None:
    _install(tmp_path)

    servers = _cli_config(tmp_path)["mcpServers"]
    assert isinstance(servers, dict)
    trw = servers["trw"]
    assert trw["type"] == "local"
    assert trw["tools"] == ["*"]
    assert trw["command"] in {"trw-mcp", "python3"}
    # VS Code Copilot Chat keeps its own file.
    assert "trw" in json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))["servers"]


def test_project_venv_launcher_is_relative_never_absolute(tmp_path: Path) -> None:
    launcher = tmp_path / ".venv" / "bin" / "trw-mcp"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    _install(tmp_path)

    trw = _cli_config(tmp_path)["mcpServers"]["trw"]  # type: ignore[index]
    assert trw["command"] == ".venv/bin/trw-mcp"
    assert str(tmp_path) not in (tmp_path / ".github" / "mcp.json").read_text(encoding="utf-8")


def test_user_servers_survive_install_and_a_user_modified_trw_entry_is_kept(tmp_path: Path) -> None:
    config = tmp_path / ".github" / "mcp.json"
    config.parent.mkdir(parents=True)
    mine = {"type": "local", "command": "my-server", "args": [], "tools": ["*"]}
    custom_trw = {"type": "local", "command": "/opt/trw/bin/trw-mcp", "args": ["--debug"], "tools": ["*"]}
    config.write_text(json.dumps({"mcpServers": {"mine": mine, "trw": custom_trw}}), encoding="utf-8")

    _install(tmp_path)

    servers = _cli_config(tmp_path)["mcpServers"]
    assert servers == {"mine": mine, "trw": custom_trw}


def test_a_trw_entry_with_custom_args_is_kept(tmp_path: Path) -> None:
    """Review r1: a known command with the user's own args is the user's entry, not TRW's."""
    from trw_mcp.bootstrap._copilot_cli_mcp import generate_copilot_cli_mcp_config

    config = tmp_path / ".github" / "mcp.json"
    config.parent.mkdir(parents=True)
    custom = {"type": "local", "command": "python3", "args": ["/opt/company/trw-wrapper.py"], "tools": ["*"]}
    config.write_text(json.dumps({"mcpServers": {"trw": custom}}), encoding="utf-8")

    result = generate_copilot_cli_mcp_config(tmp_path)

    assert not result["updated"]
    assert _cli_config(tmp_path)["mcpServers"] == {"trw": custom}


def test_a_previous_trw_launcher_is_refreshed(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._copilot_cli_mcp import copilot_cli_trw_entry, generate_copilot_cli_mcp_config

    config = tmp_path / ".github" / "mcp.json"
    config.parent.mkdir(parents=True)
    old = {"type": "local", "command": ".venv/bin/trw-mcp", "args": [], "tools": ["*"]}
    config.write_text(json.dumps({"mcpServers": {"trw": old}}), encoding="utf-8")

    result = generate_copilot_cli_mcp_config(tmp_path)  # no .venv here any more

    assert result["updated"]
    assert _cli_config(tmp_path)["mcpServers"] == {"trw": copilot_cli_trw_entry(tmp_path)}


def test_a_config_that_is_not_json_is_left_untouched(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._copilot_cli_mcp import generate_copilot_cli_mcp_config

    config = tmp_path / ".github" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text("{ not json", encoding="utf-8")

    result = generate_copilot_cli_mcp_config(tmp_path)

    assert result["errors"] and not result["created"] and not result["updated"]
    assert config.read_text(encoding="utf-8") == "{ not json"


def test_uninstall_strips_trw_and_keeps_the_users_servers(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands import _run_uninstall

    _install(tmp_path)
    config = tmp_path / ".github" / "mcp.json"
    data = _cli_config(tmp_path)
    data["mcpServers"]["mine"] = {"type": "local", "command": "my-server", "args": [], "tools": ["*"]}  # type: ignore[index]
    config.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    _run_uninstall(
        argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True, delete_memory=False, keep_memory=False)
    )

    assert _cli_config(tmp_path) == {"mcpServers": {"mine": data["mcpServers"]["mine"]}}  # type: ignore[index]
