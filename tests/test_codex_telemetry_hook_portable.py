"""E2E-CODEX-INIT-ARTIFACTS item 1 (INC-016): the codex telemetry hook names no machine path.

``.codex/hooks.json`` is committed, but its PostToolUse telemetry command was ``python3 "/abs/machine/path/
.codex/hooks/trw_post_edit_telemetry.py"``, so it broke on every other clone. Codex runs hook commands
through a shell (E2E-INC-032 observed the ``$(...)`` expansion), so it now uses the same
``$(git rev-parse --show-toplevel 2>/dev/null || pwd)`` root the PreToolUse hooks use. An install that still
carries the old absolute-path group is migrated by ``update-project`` and cleaned by ``uninstall``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_SCRIPT = "trw_post_edit_telemetry.py"


def _project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert not init_project(tmp_path, ide="codex")["errors"]
    return tmp_path


def _telemetry_commands(project: Path) -> list[str]:
    hooks = json.loads((project / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    return [
        hook["command"]
        for group in hooks.get("PostToolUse", [])
        for hook in group.get("hooks", [])
        if _SCRIPT in hook.get("command", "")
    ]


def _plant_legacy_group(project: Path) -> None:
    """Rewrite the telemetry group the way every install before this fix wrote it (absolute path)."""
    path = project / ".codex" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    legacy = f'python3 "{project.resolve() / ".codex" / "hooks" / _SCRIPT}"'
    for group in data["hooks"]["PostToolUse"]:
        for hook in group.get("hooks", []):
            if _SCRIPT in hook.get("command", ""):
                hook["command"] = legacy
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # hooks.json is a committed file in a real install. Commit, so update-project does not treat the planted
    # group as the user's uncommitted work (which preserve_uncommitted_changes rightly restores after an update).
    git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "--no-verify", "-m", "old install"], check=True)


def test_a_fresh_install_writes_a_portable_telemetry_command(tmp_path: Path) -> None:
    project = _project(tmp_path)

    commands = _telemetry_commands(project)

    assert len(commands) == 1, commands
    assert str(project.resolve()) not in commands[0]
    assert "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" in commands[0]


def test_update_migrates_an_old_absolute_path_group_to_the_portable_one(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _plant_legacy_group(project)

    assert not update_project(project, ide="codex")["errors"]

    commands = _telemetry_commands(project)
    assert len(commands) == 1, commands  # replaced, not duplicated
    assert str(project.resolve()) not in commands[0]


def test_uninstall_withdraws_an_old_absolute_path_group(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _plant_legacy_group(project)

    _run_uninstall(
        argparse.Namespace(
            target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=None
        )
    )

    hooks = project / ".codex" / "hooks.json"
    assert not hooks.exists() or _SCRIPT not in hooks.read_text(encoding="utf-8")
