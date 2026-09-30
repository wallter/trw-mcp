"""E2E-INC-032: every Codex hook command resolves its script outside a git repository.

``_trw_hook_group`` builds ``/bin/sh "$(git rev-parse --show-toplevel)/.claude/hooks/<script>"``. Codex runs it
through a shell in the session directory; in a project that is not a git checkout the expansion was empty, so
the command became ``/bin/sh "/.claude/hooks/<script>"`` and exited 127 on every hooked call.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="POSIX sh hook")


def _command(script: str) -> str:
    from trw_mcp.bootstrap._codex_hooks import _trw_hook_group

    (hook,) = _trw_hook_group(event="PreToolUse", script_name=script)["hooks"]
    return str(hook["command"])


@pytest.mark.parametrize("in_git", [False, True], ids=["not-a-git-checkout", "git-checkout"])
def test_the_hook_command_runs_the_projects_script(tmp_path: Path, in_git: bool) -> None:
    project = tmp_path / "project"
    hooks = project / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "probe.sh").write_text('printf "ran:%s" "$TRW_HOOK_CLIENT"\n', encoding="utf-8")
    if in_git:
        subprocess.run(["git", "init", "-q", str(project)], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)  # never find an enclosing repository above tmp_path

    done = subprocess.run(
        ["sh", "-c", _command("probe.sh")], cwd=project, env=env, capture_output=True, text=True, timeout=30
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("ran:"), done.stdout
    assert "not a git repository" not in done.stderr
