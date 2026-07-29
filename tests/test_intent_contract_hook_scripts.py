"""PRD-SEC-013: shell-level contract of the two bundled hooks.

These tests execute the REAL hook scripts. They exist because the fail-closed
guarantee lives in shell control flow (the ``_trw_intentional_exit`` trap), which
no Python test can prove.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "src/trw_mcp/data/hooks"
HOOKS = ("pre-tool-intent-guard.sh", "post-tool-intent-check.sh")

pytest_skip_no_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")

_LIB_STUB = """#!/bin/sh
get_repo_root() { printf '%s' "$PWD"; }
get_task_root() { printf 'docs'; }
init_hook_timer() { :; }
log_hook_execution() { return 1; }
"""

_LIB_OK = """#!/bin/sh
get_repo_root() { printf '%s' "$PWD"; }
get_task_root() { printf 'docs'; }
init_hook_timer() { :; }
log_hook_execution() { return 0; }
"""


def _project(tmp_path: Path, *, python_rc: int | None, lib: str, enrolled: bool = True) -> Path:
    project = tmp_path / "proj"
    hooks = project / ".claude/hooks"
    hooks.mkdir(parents=True)
    for name in HOOKS:
        shutil.copy2(HOOK_DIR / name, hooks / name)
    (hooks / "lib-trw.sh").write_text(lib, encoding="utf-8")
    (project / ".trw/contracts").mkdir(parents=True)
    if enrolled:
        (project / ".trw/contracts/enrollment.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    fake_bin = project / "fakebin"
    fake_bin.mkdir()
    script = "#!/bin/sh\nsleep 5\n" if python_rc is None else f"#!/bin/sh\nexit {python_rc}\n"
    (fake_bin / "python3").write_text(script, encoding="utf-8")
    (fake_bin / "python3").chmod((fake_bin / "python3").stat().st_mode | stat.S_IXUSR)
    return project


def _run(project: Path, hook: str) -> int:
    env = {
        "PATH": f"{project / 'fakebin'}:{os.environ.get('PATH', '')}",
        "HOME": str(project),
        "TRW_INTENT_PRE_WRITE_BUDGET_SECONDS": "1",
        "TRW_INTENT_POST_EDIT_BUDGET_SECONDS": "1",
    }
    completed = subprocess.run(
        ["sh", str(project / ".claude/hooks" / hook)],
        cwd=str(project),
        input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "protected/module.py"}}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        shell=False,
    )
    return completed.returncode


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", HOOKS)
def test_logging_failure_never_converts_a_block_into_an_allow(tmp_path: Path, hook: str) -> None:
    """R4 class: log_hook_execution returning nonzero must not reach the exit path."""
    project = _project(tmp_path, python_rc=2, lib=_LIB_STUB)
    assert _run(project, hook) == 2


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", HOOKS)
def test_logging_failure_on_the_allow_path_still_allows(tmp_path: Path, hook: str) -> None:
    project = _project(tmp_path, python_rc=0, lib=_LIB_STUB)
    assert _run(project, hook) == 0


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", HOOKS)
def test_timeout_fails_closed(tmp_path: Path, hook: str) -> None:
    if shutil.which("timeout") is None:
        pytest.skip("timeout unavailable")
    project = _project(tmp_path, python_rc=None, lib=_LIB_OK)
    assert _run(project, hook) == 2


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", HOOKS)
def test_missing_interpreter_fails_closed_when_enrolled(tmp_path: Path, hook: str) -> None:
    """An enrolled project with no python3 cannot verify anything — so it blocks."""
    project = _project(tmp_path, python_rc=0, lib=_LIB_OK)
    (project / "fakebin/python3").unlink()
    # A minimal PATH carrying only the utilities the hook itself needs, so
    # `command -v python3` genuinely fails.
    minbin = project / "minbin"
    minbin.mkdir()
    for tool in ("sh", "dirname", "cat", "timeout", "printf", "git"):
        located = shutil.which(tool)
        if located:
            (minbin / tool).symlink_to(located)
    completed = subprocess.run(
        [shutil.which("sh") or "sh", str(project / ".claude/hooks" / hook)],
        cwd=str(project),
        input="{}",
        capture_output=True,
        text=True,
        env={"PATH": str(minbin), "HOME": str(project)},
        check=False,
        shell=False,
    )
    assert completed.returncode == 2


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", HOOKS)
def test_never_enrolled_is_a_clean_no_op(tmp_path: Path, hook: str) -> None:
    project = _project(tmp_path, python_rc=2, lib=_LIB_OK, enrolled=False)
    assert _run(project, hook) == 0


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", HOOKS)
def test_working_tree_deletion_of_a_tracked_marker_does_not_disarm_the_hook(tmp_path: Path, hook: str) -> None:
    """`rm .trw/contracts/enrollment.yaml` must not turn the hook into a no-op."""
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    project = _project(tmp_path, python_rc=2, lib=_LIB_OK)
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "sec013@example.test"),
        ("config", "user.name", "sec013"),
        ("add", "-A"),
        ("-c", "commit.gpgsign=false", "commit", "-qm", "enroll"),
    ):
        subprocess.run(["git", *args], cwd=str(project), check=True, capture_output=True, shell=False)

    (project / ".trw/contracts/enrollment.yaml").unlink()
    assert _run(project, hook) == 2
