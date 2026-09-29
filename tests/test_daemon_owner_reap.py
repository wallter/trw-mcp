"""A test session stops every memory daemon it started, however it was placed (DAEMON-ORPHAN-SPAWN, 2026-09-26).

The host ran 11 orphaned ``trw_memory serve http`` daemons at load 26. Test daemons detach on purpose, so a
parent pid says nothing about ownership; each session instead tags the environment its daemons inherit
(``TRW_PYTEST_DAEMON_OWNER``) and caps their idle life at 60 s, so one a killed session could not reap still exits.
The reap-by-owner arm itself is tested with the shared reaper, in trw-memory's
``tests/test_testing_daemon_reaper.py``; these arms test trw-mcp's own session and child-env builders.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from trw_memory.testing.daemon_reaper import IDLE_VARIABLE, OWNER_VARIABLE

from tests._stdio_harness import StdioServerHarness

pytestmark = pytest.mark.integration

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_PRINT_DAEMON_ENV = (
    f"import os; print(os.environ.get('{OWNER_VARIABLE}', '<unset>'), os.environ.get('{IDLE_VARIABLE}'))"
)


def test_a_daemon_inherits_the_session_owner_and_the_short_idle_cap(tmp_path: Path) -> None:
    """Idle arm: the child a client would auto-start sees this session's owner and a 60 s idle shutdown."""
    probe = (
        "import os; from trw_memory.models.config import MemoryConfig; "
        f"print(os.environ['{OWNER_VARIABLE}'], MemoryConfig().memory_daemon_idle_shutdown_seconds)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=tmp_path)
    owner, idle = out.stdout.split()
    assert owner == os.environ[OWNER_VARIABLE]
    assert idle == "60"


def test_a_child_of_the_stdio_harness_sanitized_env_still_carries_the_owner(tmp_path: Path) -> None:
    """Allowlisted-env arm: the harness drops every inherited ``TRW_*``, yet a daemon its child starts is still ours."""
    harness = StdioServerHarness(tmp_path / "project", tmp_path / "user", tmp_path / "stderr")
    env = harness.child_env("probe")
    out = subprocess.run([sys.executable, "-c", _PRINT_DAEMON_ENV], capture_output=True, text=True, check=True, env=env)

    assert out.stdout.split() == [os.environ[OWNER_VARIABLE], os.environ[IDLE_VARIABLE]]
    # Nothing else inherited from the TRW_* namespace rides along with the owner token.
    assert {key for key in env if key.startswith("TRW_")} == {
        "TRW_PROJECT_ROOT",
        "TRW_USER_DIR",
        "TRW_SESSION_ID",
        "TRW_HOT_PATH_STRICT",
        OWNER_VARIABLE,
    }


def test_a_session_that_returns_with_a_detached_daemon_running_leaves_none_alive(tmp_path: Path) -> None:
    """End to end: an inner session starts a detached daemon outside its basetemp and returns; none survives."""
    run = uuid.uuid4().hex[:12]
    pid_file = tmp_path / f"daemon-{run}.pid"
    elsewhere = tmp_path / f"elsewhere-{run}"
    elsewhere.mkdir()
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_leaks.py").write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys
            from pathlib import Path

            def test_starts_a_daemon_and_returns():
                env = {{**os.environ, "HOME": {str(elsewhere)!r}, "TRW_USER_DIR": {str(elsewhere)!r}}}
                daemon = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(120)", "trw_memory.server", "serve", "http"],
                    env=env, cwd={str(elsewhere)!r}, start_new_session=True,
                )
                Path({str(pid_file)!r}).write_text(str(daemon.pid))
            """
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in (OWNER_VARIABLE, "PYTEST_XDIST_WORKER")}
    result = subprocess.run(
        [
            *[sys.executable, "-m", "pytest", str(inner), "-p", "tests.conftest", "-q"],
            *["-p", "no:cacheprovider", "-p", "no:randomly", f"--rootdir={inner}"],
            f"--basetemp={tmp_path / 'inner-basetemp'}",
        ],
        capture_output=True,
        text=True,
        cwd=_PACKAGE_ROOT,
        env=env,
        check=False,
        timeout=180,
    )
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    try:
        assert not _alive(pid), f"the inner session left daemon {pid} running:\n{result.stdout}\n{result.stderr}"
        assert "leaked memory daemon" in result.stderr, "the leak still fails the session that caused it"
    finally:
        if _alive(pid):
            os.kill(pid, 9)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # trw-fail-silent-allow: no such process means not alive
        return False
    return True
