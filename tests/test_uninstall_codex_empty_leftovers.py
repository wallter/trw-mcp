"""E2E-UNINSTALL-EMPTY-DIRS (INC-012 residual): init --ide codex then uninstall leaves no TRW-made husks.

Two leftovers survived the dangling-reference fix: an empty ``.agents/`` (uninstall pruned ``.agents/skills``
but not its now-empty parent) and a ``.git/hooks/post-commit`` holding only the header TRW's fresh shim
writes above its managed block. A hook the user wrote, or any directory with something in it, stays.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=None
    )


def _codex_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert not init_project(tmp_path, ide="codex")["errors"]
    return tmp_path


def test_uninstall_leaves_no_empty_agents_dir_and_no_header_only_hook(tmp_path: Path) -> None:
    project = _codex_project(tmp_path)
    hook = project / ".git" / "hooks" / "post-commit"
    assert (project / ".agents").is_dir(), "precondition: init wrote .agents/"
    assert hook.is_file(), "precondition: init wrote the post-commit shim"

    _run_uninstall(_ns(project))

    assert not (project / ".agents").exists()
    assert not hook.exists(), hook.read_text(encoding="utf-8") if hook.exists() else ""


def test_a_users_own_post_commit_logic_and_agents_content_stay(tmp_path: Path) -> None:
    """The controls: user lines in the hook keep the file; a user file under .agents keeps the dir."""
    project = _codex_project(tmp_path)
    hook = project / ".git" / "hooks" / "post-commit"
    hook.write_text(hook.read_text(encoding="utf-8") + "\necho mine\n", encoding="utf-8")
    (project / ".agents" / "notes.md").write_text("mine\n", encoding="utf-8")

    _run_uninstall(_ns(project))

    assert "echo mine" in hook.read_text(encoding="utf-8")
    assert (project / ".agents" / "notes.md").read_text(encoding="utf-8") == "mine\n"
