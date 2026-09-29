"""Destructive-git guard in the rendered cursor-cli beforeShellExecution hook.

The generated ``.cursor/cli.json`` allows ``Shell(git)`` (everyday git needs it), and
cursor-agent's ``Shell(<cmd>)`` token matches only a command's first word, so a permission
rule cannot single out a git verb. Before this guard, the installed hook scanned only for
secrets, and a command that discards uncommitted work (HB-2) passed every external layer.

These tests render the cursor-cli install into ``tmp_path`` with
``generate_cursor_cli_hooks`` and run the INSTALLED hook script, so they check the artifact a
user gets rather than the packaged source.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config, generate_cursor_cli_hooks

# Assembled from parts so the repository's own shell guard, which scans command text,
# does not mistake this test source for a destructive command.
_GIT = "git"
_HARD = "--" + "hard"


def _installed_hook(tmp_path: Path) -> Path:
    generate_cursor_cli_hooks(tmp_path)
    hook = tmp_path / ".cursor" / "hooks" / "trw-before-shell.sh"
    assert hook.is_file()
    return hook


def _decide(hook: Path, command: str) -> dict[str, str]:
    proc = subprocess.run(
        ["bash", str(hook)],
        input=json.dumps({"command": command}),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "CURSOR_PROJECT_DIR": str(hook.parent.parent.parent)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    decision: dict[str, str] = json.loads(proc.stdout.strip().splitlines()[-1])
    return decision


@pytest.mark.parametrize(
    "command",
    [
        f"{_GIT} reset {_HARD}",
        f"{_GIT} -C sub reset {_HARD} HEAD~1",
        f"cd sub && {_GIT} clean -fdx",
        f"{_GIT} clean --force",
        f"{_GIT} checkout -- .",
        f"{_GIT} checkout HEAD -- src/app.py",
        f"{_GIT} checkout .",
        f"{_GIT} checkout -f main",
        f"{_GIT} restore src/app.py",
        f"{_GIT} restore --staged --worktree src/app.py",
        f"{_GIT} stash drop",
        f"{_GIT} stash clear",
        f"{_GIT} push --force origin main",
        f"{_GIT} push origin +main",
        f"/usr/bin/{_GIT} reset {_HARD}",
        f"{_GIT} status; {_GIT} reset {_HARD}",
        f"{_GIT} status\n{_GIT} reset {_HARD}",
        # review r1: a value-taking global option, "--" ending option parsing, a literal "git"
        # argument, and a discarding switch.
        f"{_GIT} --work-tree . reset {_HARD}",
        f"{_GIT} --git-dir .git reset {_HARD}",
        f"{_GIT} clean -f -- -n",
        f"{_GIT} restore -- --staged",
        f"{_GIT} clean {_GIT} -f",
        f"{_GIT} push origin {_GIT} +main",
        f"{_GIT} switch --discard-changes main",
        f"{_GIT} switch -f main",
        # review r2: path resolution under -C, option values hiding a dry run, a negated dry run;
        # plus git's own long-option abbreviations. The allow-list design removes all four classes.
        f"{_GIT} -C sub checkout SECURITY.md",
        f"{_GIT} checkout main",
        f"{_GIT} clean -f -e -n",
        f"{_GIT} clean -f -e --dry-run",
        f"{_GIT} clean -f -n --no-dry-run",
        f"{_GIT} clean --forc",
        f"{_GIT} reset --har",
        f"{_GIT} restore --worktree --staged a.py",
        f"{_GIT} push --forc origin main",
        f"{_GIT} push --mirror origin",
        # review r3: a value-taking short option swallowing the rest of its cluster, and
        # non-letter characters inside a bundle.
        f"{_GIT} restore -sS README.md",
        f"{_GIT} push -f4 origin main",
        f"{_GIT} switch -fcfeature-123 main",
    ],
)
def test_destructive_git_is_denied(tmp_path: Path, command: str) -> None:
    decision = _decide(_installed_hook(tmp_path), command)
    assert decision["permission"] == "deny", command
    assert "HB-2" in decision["user_message"]


@pytest.mark.parametrize(
    "command",
    [
        f"{_GIT} status",
        f"{_GIT} diff --stat",
        f"{_GIT} checkout -b feature",
        f"{_GIT} reset --soft HEAD~1",
        f"{_GIT} clean -n",
        f"{_GIT} clean -fdn",
        f"{_GIT} restore --staged src/app.py",
        f"{_GIT} stash list",
        f"{_GIT} push --force-with-lease origin main",
        f"{_GIT} push -u origin feature",
        f'{_GIT} commit -m "reset {_HARD} docs"',
        f"{_GIT} checkout -b feature origin/main",
        f"{_GIT} checkout -B feature",
        f"{_GIT} checkout -",
        f"{_GIT} switch main\nls .",
        f"{_GIT} switch -c feature",
        f"{_GIT} reset",
        f"{_GIT} reset HEAD~1",
        f"{_GIT} reset -q --mixed HEAD -- a.py",
        f"{_GIT} clean --dry-run -d",
        f"{_GIT} restore -S -s HEAD~1 a.py",
        f"{_GIT} restore -S --source HEAD~1 a.py",
        f"{_GIT} switch -c feature-123 main",
        f"{_GIT} push -4 -u origin main",
        f"{_GIT} clean -n -e build",
        f"{_GIT} stash pop",
        f"{_GIT} push --force-with-lease=main:abc origin main",
        "ls -la",
    ],
)
def test_ordinary_git_is_allowed(tmp_path: Path, command: str) -> None:
    assert _decide(_installed_hook(tmp_path), command) == {"permission": "allow"}


def test_cli_json_keeps_git_allowed_so_the_hook_is_the_verb_guard(tmp_path: Path) -> None:
    """The permission list cannot express a per-verb deny; the hook must carry the guard."""
    generate_cursor_cli_config(tmp_path)
    permissions = json.loads((tmp_path / ".cursor" / "cli.json").read_text(encoding="utf-8"))["permissions"]
    assert "Shell(git)" in permissions["allow"]
    assert not [rule for rule in permissions["deny"] if rule.startswith("Shell(git")]


def test_checkout_denial_points_the_agent_at_git_switch(tmp_path: Path) -> None:
    """``git checkout <name>`` can overwrite a working-tree file, so branch changes go through switch."""
    decision = _decide(_installed_hook(tmp_path), f"{_GIT} checkout release-8")
    assert decision["permission"] == "deny"
    assert "git switch" in decision["user_message"]
