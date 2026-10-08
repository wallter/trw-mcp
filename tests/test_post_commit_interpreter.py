"""PRD-FIX-156 s2 (L-7zca): the post-commit sweep runs under an interpreter that can do it, or says why not.

The hook reads ``.trw/channels/cc03-python.txt`` and otherwise runs ``python3``
from PATH. Nothing wrote that file, so the sweep ran under an interpreter that
lacked trw-mcp's dependencies and failed inside the detached worker, where
nobody saw it; the older hook tests passed because a receipt was written at all.
These tests install through the real installer, make a REAL commit, and require
a receipt with no errors -- or, with an unusable interpreter, a loud refusal.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from tests.test_git_post_commit_hook_install import _commit, git_repo  # noqa: F401 - git_repo is a fixture
from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook
from trw_mcp.bootstrap._hook_interpreter import HOOK_INTERPRETER_REL, record_hook_interpreter
from trw_mcp.tools._post_commit import read_receipt


def test_the_installer_records_the_interpreter_running_it(git_repo: Path) -> None:
    result = install_git_post_commit_hook(git_repo)

    assert (git_repo / HOOK_INTERPRETER_REL).read_text(encoding="utf-8") == sys.executable + "\n"
    assert str(git_repo / HOOK_INTERPRETER_REL) in result["updated"]
    assert result["errors"] == []


def test_record_replaces_a_stale_interpreter_and_is_idempotent(tmp_path: Path) -> None:
    stale = tmp_path / HOOK_INTERPRETER_REL
    stale.parent.mkdir(parents=True)
    stale.write_text("/gone/venv/bin/python\n", encoding="utf-8")

    assert record_hook_interpreter(tmp_path, executable="/opt/venv/bin/python") == stale
    assert stale.read_text(encoding="utf-8") == "/opt/venv/bin/python\n"
    assert record_hook_interpreter(tmp_path, executable="/opt/venv/bin/python") is None


def test_record_dry_run_writes_nothing_and_a_relative_path_is_refused(tmp_path: Path) -> None:
    assert record_hook_interpreter(tmp_path, dry_run=True, executable="/opt/py") == tmp_path / HOOK_INTERPRETER_REL
    assert not (tmp_path / HOOK_INTERPRETER_REL).exists()
    with pytest.raises(ValueError, match="absolute"):
        record_hook_interpreter(tmp_path, executable="python3")


def test_record_never_writes_through_a_symlinked_channels_dir(tmp_path: Path) -> None:
    """sol s2 r1: a checkout that ships .trw/channels as a link must not get a file
    outside it replaced."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cc03-python.txt").write_text("KEEP\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "channels").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="symlink"):
        record_hook_interpreter(project, executable="/opt/venv/bin/python")
    assert (outside / "cc03-python.txt").read_text(encoding="utf-8") == "KEEP\n"
    assert install_git_post_commit_hook(project)["errors"], "the installer reports the refusal"


def test_a_hanging_interpreter_cannot_hold_the_commit(git_repo: Path, tmp_path: Path) -> None:
    """A detached worker cannot hold the commit or its captured output pipes."""
    install_git_post_commit_hook(git_repo)
    hanging = tmp_path / "python-that-hangs"
    hanging.write_text("#!/bin/sh\nsleep 3\n", encoding="utf-8")  # ignores SIGTERM too
    hanging.chmod(0o755)
    (git_repo / HOOK_INTERPRETER_REL).write_text(f"{hanging}\n", encoding="utf-8")
    (git_repo / "c.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "c.py"], cwd=git_repo, check=True)

    started = time.monotonic()
    commit = subprocess.run(
        ["git", "commit", "-qm", "add c"],
        cwd=git_repo,
        env={**os.environ, "TRW_PROJECT_DIR": str(git_repo)},
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert commit.returncode == 0
    assert time.monotonic() - started < 2
    assert "maintenance skipped" not in commit.stderr


def test_a_real_commit_runs_the_sweep_without_errors(
    git_repo: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The discriminating check L-7zca asked for: the receipt's errors stay empty only
    when the sidecar refresh and the verify sweep both really ran against a store."""
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # the hook's worker inherits it
    namespace, client = attach_checkout(git_repo / ".trw", memory_daemon)
    assertion = {"type": "grep_present", "pattern": "x = 1", "target": "a.py"}
    asyncio.run(client.store("a.py sets x", namespace, assertions=[assertion]))
    install_git_post_commit_hook(git_repo)  # pins the interpreter the hook resolves (Q7 item 8)

    _commit(git_repo, "a.py")

    receipt = read_receipt(git_repo)
    assert receipt is not None, "the hook did not fire"
    assert receipt["errors"] == [], receipt["errors"]
    # Only a sweep that really read the store sets this; a worker that could not import
    # the store's dependencies leaves it at 0 with an error.
    assert receipt["verify_entries_processed"] >= 1


def test_an_interpreter_without_trw_mcp_is_refused_loudly(git_repo: Path, tmp_path: Path) -> None:
    install_git_post_commit_hook(git_repo)
    unusable = tmp_path / "python-without-trw"
    unusable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    unusable.chmod(0o755)
    (git_repo / HOOK_INTERPRETER_REL).write_text(f"{unusable}\n", encoding="utf-8")
    (git_repo / "b.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "b.py"], cwd=git_repo, check=True)

    commit = subprocess.run(
        ["git", "commit", "-qm", "add b"],
        cwd=git_repo,
        env={**os.environ, "TRW_PROJECT_DIR": str(git_repo), "TRW_POST_COMMIT_SYNC": "1"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert commit.returncode == 0, "a hook problem must never fail the commit"
    assert "worker failed to start or run" in commit.stderr
    log = (git_repo / ".trw" / "context" / "hook-executions.log").read_text(encoding="utf-8")
    assert "event=PostCommit" in log and "python_unavailable=1" in log
    assert read_receipt(git_repo) is None, "no sweep may be reported for a run that could not start"
