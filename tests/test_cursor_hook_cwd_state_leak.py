"""HOOK-CWD-STATE-LEAK (Cursor follow-up): Cursor's logging hooks write under the project root, not the shell's CWD.

``trw-before-shell.sh``, ``trw-after-shell.sh`` and ``trw-after-mcp.sh`` logged to
``${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs``: without ``CURSOR_PROJECT_DIR`` a hook run from a subdirectory created
``.trw/logs`` there. They now fall back to the git top level before ``pwd``. The before-shell hook is a fail-closed
security gate under ``set -euo pipefail``, so its decision must also be unchanged outside a git repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh hook"),
]

_CURSOR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / "cursor"
_HOOKS = {
    "trw-before-shell.sh": {"command": "echo hello", "cwd": "."},
    "trw-after-shell.sh": {"command": "echo hello", "output": "hello"},
    "trw-after-mcp.sh": {"tool_name": "trw_status", "result_json": "{}"},
}


def _run(hook: Path, cwd: Path, payload: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CURSOR_PROJECT_DIR", "CLAUDE_PROJECT_DIR")}
    return subprocess.run(
        ["bash", str(hook)], input=json.dumps(payload), cwd=cwd, env=env, capture_output=True, text=True, timeout=30
    )


@pytest.mark.parametrize("name", sorted(_HOOKS))
def test_cursor_hook_run_from_a_subdirectory_logs_under_the_project_root(tmp_path: Path, name: str) -> None:
    root = tmp_path / "project"
    subdir = root / "src" / "pkg"
    subdir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    hook = shutil.copy2(_CURSOR / name, root / name)

    _run(Path(hook), subdir, _HOOKS[name])

    assert not (subdir / ".trw").exists()
    assert (root / ".trw" / "logs").is_dir()


def test_before_shell_still_decides_outside_a_git_repo(tmp_path: Path) -> None:
    """The fail-closed gate must not die on the git fallback (set -e) when there is no repository."""
    proc = _run(_CURSOR / "trw-before-shell.sh", tmp_path, _HOOKS["trw-before-shell.sh"])

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1])["permission"] == "allow"
