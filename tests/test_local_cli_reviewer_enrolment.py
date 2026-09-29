"""PRD-CORE-305-FR05 (B71-23) — local learn/checkpoint/init are refused under a bounded lane.

R6 left ``local learn``, ``local checkpoint``, and ``local init`` off any
CLI guard's radar, so a reviewer-role refusal of these verbs depended on the
caller's sandbox. Sol round-1 P0 replaced the original allow-by-default
enrolment (``LOCAL_STATE_CHANGING_COMMANDS``, removed) with the deny-by-default
class-level fix in ``server/_cli_reviewer_policy.py``: a command is permitted
under ``TRW_SURFACE_ROLE=reviewer``/``TRW_DISPATCH_CHILD`` only when it is
explicitly named in that module's read-only allowlist. These three verbs are
simply absent from it (see ``test_cli_reviewer_deny_by_default.py`` for the
whole-surface coverage proof), so they are refused the same way any
unclassified verb is -- no per-verb enrolment needed any more.

The CLI is invoked in a real subprocess (``python -m trw_mcp.server``, the
production entry point) with an explicit, absolute ``PYTHONPATH`` — the
worktree's ``.venv`` has an EDITABLE install of ``trw_mcp`` pointing at a
DIFFERENT checkout, and a relative ``PYTHONPATH`` (as set for this test run)
silently stops resolving once the subprocess's cwd is a scratch directory,
which would make the subprocess exercise stale code, not this file's fix.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout
from tests._stdio_harness import pinned_server_env
from trw_mcp.server._cli_reviewer_policy import classified_command_paths

pytestmark = pytest.mark.integration

_LOCAL_STATE_CHANGING_VERBS = ("local init", "local checkpoint", "local learn")

#: This test's own package sources, absolute, so the subprocess resolves THIS
#: checkout's trw_mcp regardless of the editable install pinned in .venv or
#: the subprocess's own cwd (see module docstring).
_TRW_MCP_SRC = str(Path(__file__).resolve().parents[1] / "src")
_TRW_MEMORY_SRC = str(Path(__file__).resolve().parents[2] / "trw-memory" / "src")


def _run_cli(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the real ``trw-mcp`` CLI entry as a subprocess (the real argparse dispatch)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_TRW_MCP_SRC, _TRW_MEMORY_SRC])
    return subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=pinned_server_env(env),
    )


@pytest.mark.parametrize("command", _LOCAL_STATE_CHANGING_VERBS)
def test_local_verb_is_not_on_the_read_only_allowlist(command: str) -> None:
    assert command not in classified_command_paths(), (
        f"{command!r} is on the reviewer read-only allowlist; it writes and must not be"
    )


def test_local_init_refused_under_reviewer_role_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = tmp_path / ".trw"
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    result = _run_cli(["local", "init", "--task", "fr05-refuse"], tmp_path)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "local init" in result.stderr
    assert not trw_dir.exists(), "a refused 'local init' must not create .trw/"


def test_local_checkpoint_refused_under_reviewer_role_and_writes_nothing(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = daemon_checkout.trw_dir.parent
    init_result = _run_cli(["local", "init", "--task", "fr05-contract"], project_root)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr
    run_path = next(
        line.split("Path:", 1)[1].strip()
        for line in init_result.stdout.splitlines()
        if line.strip().startswith("Path:")
    )
    checkpoints_file = Path(run_path) / "meta" / "checkpoints.jsonl"
    before = checkpoints_file.read_bytes() if checkpoints_file.exists() else b""

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    result = _run_cli(["local", "checkpoint", "--run-path", run_path, "--message", "should be refused"], project_root)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "local checkpoint" in result.stderr
    after = checkpoints_file.read_bytes() if checkpoints_file.exists() else b""
    assert after == before, "a refused 'local checkpoint' must not write checkpoints.jsonl"


def test_local_learn_refused_under_reviewer_role_and_writes_nothing(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = daemon_checkout.trw_dir.parent

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    result = _run_cli(
        ["local", "learn", "--summary", "should be refused", "--detail", "should be refused detail"],
        project_root,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "local learn" in result.stderr

    recall = _run_cli(["local", "recall", "--query", "should be refused"], project_root)
    assert "should be refused" not in recall.stdout, "a refused 'local learn' must not write to the learnings store"


def test_local_init_still_works_without_reviewer_role(tmp_path: Path) -> None:
    result = _run_cli(["local", "init", "--task", "fr05-allowed"], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_local_checkpoint_still_works_without_reviewer_role(daemon_checkout: DaemonCheckout) -> None:
    project_root = daemon_checkout.trw_dir.parent
    init_result = _run_cli(["local", "init", "--task", "fr05-allowed-cp"], project_root)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr
    run_path = next(
        line.split("Path:", 1)[1].strip()
        for line in init_result.stdout.splitlines()
        if line.strip().startswith("Path:")
    )

    result = _run_cli(["local", "checkpoint", "--run-path", run_path, "--message", "allowed"], project_root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_local_learn_still_works_without_reviewer_role(daemon_checkout: DaemonCheckout) -> None:
    project_root = daemon_checkout.trw_dir.parent
    result = _run_cli(["local", "learn", "--summary", "allowed", "--detail", "allowed detail"], project_root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dispatched_child_env_also_refuses_local_learn(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard reads two independent env markers; this covers the second one."""
    project_root = daemon_checkout.trw_dir.parent
    monkeypatch.setenv("TRW_DISPATCH_CHILD", "1")
    result = _run_cli(
        ["local", "learn", "--summary", "should be refused", "--detail", "should be refused detail"],
        project_root,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "local learn" in result.stderr
