"""N17: every client config launches the PROJECT's trw-mcp before any PATH install.

Observed 2026-09-19: grok and agy sessions in the monorepo ran a stale PyPI trw-mcp
3.1.0 from PATH because only the codex generator preferred ``.venv/bin/trw-mcp``.
Every generator now uses ``bootstrap._utils.resolve_trw_mcp_launcher``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _utils
from trw_mcp.bootstrap._codex import _trw_mcp_server_entry as codex_entry
from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config
from trw_mcp.bootstrap._mcp_json import _merge_mcp_json
from trw_mcp.bootstrap._opencode import generate_opencode_config
from trw_mcp.bootstrap._utils import resolve_trw_mcp_launcher
from trw_mcp.channels.copilot._vscode_mcp import generate_vscode_mcp_config

pytestmark = pytest.mark.unit


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with its own venv build AND an (older) trw-mcp on PATH."""
    launcher = tmp_path / ".venv" / "bin" / "trw-mcp"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    monkeypatch.setattr(_utils.shutil, "which", lambda name: "/usr/local/bin/trw-mcp")
    return tmp_path


def test_resolver_order_project_venv_then_path_then_python(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_trw_mcp_launcher(project) == (".venv/bin/trw-mcp", [])
    assert resolve_trw_mcp_launcher(project, root_prefix="${workspaceFolder}/") == (
        "${workspaceFolder}/.venv/bin/trw-mcp",
        [],
    )
    assert resolve_trw_mcp_launcher(None) == ("trw-mcp", []), "no project: PATH"
    monkeypatch.setattr(_utils.shutil, "which", lambda name: None)
    assert resolve_trw_mcp_launcher(project.parent / "elsewhere") == ("python3", ["-m", "trw_mcp.server"])


def test_windows_project_venv_is_found(tmp_path: Path) -> None:
    exe = tmp_path / ".venv" / "Scripts" / "trw-mcp.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert resolve_trw_mcp_launcher(tmp_path)[0] == ".venv/Scripts/trw-mcp.exe"


def test_never_a_machine_absolute_path(project: Path) -> None:
    command, _ = resolve_trw_mcp_launcher(project)
    assert not Path(command).is_absolute() and str(project) not in command


def test_claude_mcp_json_uses_the_project_build(project: Path) -> None:
    _merge_mcp_json(project, {"created": [], "updated": [], "preserved": [], "errors": []})
    entry = json.loads((project / ".mcp.json").read_text())["mcpServers"]["trw"]
    assert entry["command"] == ".venv/bin/trw-mcp"


def test_codex_uses_the_project_build(project: Path) -> None:
    assert codex_entry(project)["command"] == ".venv/bin/trw-mcp"


def test_cursor_uses_the_project_build_via_workspace_folder(project: Path) -> None:
    generate_cursor_mcp_config(project)
    entry = json.loads((project / ".cursor" / "mcp.json").read_text())["mcpServers"]["trw"]
    assert entry["command"] == "${workspaceFolder}/.venv/bin/trw-mcp"


def test_opencode_uses_the_project_build(project: Path) -> None:
    generate_opencode_config(project)
    config = json.loads((project / "opencode.json").read_text())
    assert config["mcp"]["trw"]["command"] == [".venv/bin/trw-mcp"]


def test_vscode_refreshes_the_old_path_default_and_keeps_a_user_edit(project: Path) -> None:
    vscode = project / ".vscode" / "mcp.json"
    vscode.parent.mkdir()
    vscode.write_text(json.dumps({"servers": {"trw": {"args": [], "command": "trw-mcp", "type": "stdio"}}}))
    generate_vscode_mcp_config(project)
    assert json.loads(vscode.read_text())["servers"]["trw"]["command"] == "${workspaceFolder}/.venv/bin/trw-mcp"
    custom = {"args": ["--x"], "command": "/opt/my/trw-mcp", "type": "stdio"}
    vscode.write_text(json.dumps({"servers": {"trw": custom}}))
    generate_vscode_mcp_config(project)
    assert json.loads(vscode.read_text())["servers"]["trw"] == custom, "a user's own entry is preserved"


def _launchers(root: Path) -> dict[str, object]:
    import tomllib

    return {
        ".mcp.json": json.loads((root / ".mcp.json").read_text())["mcpServers"]["trw"]["command"],
        ".codex/config.toml": tomllib.loads((root / ".codex" / "config.toml").read_text())["mcp_servers"]["trw"][
            "command"
        ],
        ".cursor/mcp.json": json.loads((root / ".cursor" / "mcp.json").read_text())["mcpServers"]["trw"]["command"],
        "opencode.json": json.loads((root / "opencode.json").read_text())["mcp"]["trw"]["command"],
        ".vscode/mcp.json": json.loads((root / ".vscode" / "mcp.json").read_text())["servers"]["trw"]["command"],
    }


_EXPECTED = {
    ".mcp.json": ".venv/bin/trw-mcp",
    ".codex/config.toml": ".venv/bin/trw-mcp",
    ".cursor/mcp.json": "${workspaceFolder}/.venv/bin/trw-mcp",
    "opencode.json": [".venv/bin/trw-mcp"],
    ".vscode/mcp.json": "${workspaceFolder}/.venv/bin/trw-mcp",
}


def test_init_and_update_project_write_the_project_build_for_every_managed_config(project: Path) -> None:
    """lead N17 addition: init-project AND update-project pass the project root. The
    realistic migration: configs written by an install with no project venv (PATH
    launcher, hashes recorded as TRW-managed), then the project gains .venv/bin/trw-mcp;
    update-project must move every managed config onto it."""
    import subprocess

    from trw_mcp.bootstrap._init_project import init_project
    from trw_mcp.bootstrap._update_project import update_project

    venv = project / ".venv"
    venv.rename(project / "venv-later")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    init_project(project, force=True, ide="all")
    assert set(map(str, _launchers(project).values())) <= {"trw-mcp", "['trw-mcp']"}, "PATH launcher before the venv"
    # A real project has its configs COMMITTED; update-project deliberately restores
    # uncommitted files (preserve_uncommitted_changes), so commit like a real install.
    git = ["git", "-C", str(project), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "trw init"], check=True)
    (project / "venv-later").rename(venv)
    update_project(project)
    assert _launchers(project) == _EXPECTED
    init_project(project, force=True, ide="all")
    assert _launchers(project) == _EXPECTED, "init-project writes the same"
