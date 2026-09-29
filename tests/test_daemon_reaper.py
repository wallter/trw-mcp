"""trw-mcp's session-end sweep fails the suite on a leaked memory daemon, serial and under xdist.

The reaper itself (discovery file, placement, owner, spawn handle) is shared with
trw-memory's suite and tested there: ``trw-memory/tests/test_testing_daemon_reaper.py``.
These tests pin how trw-mcp's own ``conftest`` wires it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from trw_memory.testing.daemon_reaper import SessionSweep

pytestmark = pytest.mark.integration


# ── PRD-INFRA-196-FR07: the suite itself fails when the session-end sweep found a leak ──


def test_pytest_sessionfinish_fails_the_session_when_the_sweep_reaped_a_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.conftest as conftest_mod

    class _FakeSession:
        exitstatus = 0

        class config:
            _tmp_path_factory = type("F", (), {"getbasetemp": staticmethod(lambda: Path("/tmp/x"))})()
            stash = pytest.Stash()

    monkeypatch.setattr(
        conftest_mod, "sweep_session_daemons", lambda basetemp, _owner: SessionSweep(basetemp, [12345], [])
    )
    monkeypatch.setattr(conftest_mod, "_timing_sessionfinish", lambda *_a, **_k: None)
    session = _FakeSession()

    conftest_mod.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert session.exitstatus == 1


def test_pytest_sessionfinish_leaves_a_clean_session_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.conftest as conftest_mod

    class _FakeSession:
        exitstatus = 0

        class config:
            _tmp_path_factory = type("F", (), {"getbasetemp": staticmethod(lambda: Path("/tmp/x"))})()
            stash = pytest.Stash()

    monkeypatch.setattr(conftest_mod, "sweep_session_daemons", lambda basetemp, _owner: SessionSweep(basetemp, [], []))
    monkeypatch.setattr(conftest_mod, "_timing_sessionfinish", lambda *_a, **_k: None)
    session = _FakeSession()

    conftest_mod.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert session.exitstatus == 0


def test_pytest_sessionfinish_fails_the_session_when_a_daemon_survives_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The survivor guard: nothing leaked by this sweep's count, but a daemon is still alive after it."""
    import tests.conftest as conftest_mod

    class _FakeSession:
        exitstatus = 0

        class config:
            _tmp_path_factory = type("F", (), {"getbasetemp": staticmethod(lambda: Path("/tmp/x"))})()
            stash = pytest.Stash()

    monkeypatch.setattr(
        conftest_mod, "sweep_session_daemons", lambda basetemp, _owner: SessionSweep(basetemp, [], [4242])
    )
    monkeypatch.setattr(conftest_mod, "_timing_sessionfinish", lambda *_a, **_k: None)
    session = _FakeSession()

    conftest_mod.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert session.exitstatus == 1


_PLANTED_LEAK_TEST = """
import subprocess, sys, time

def test_leaks_a_daemon():
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "trw_memory.server", "serve"])
    time.sleep(0.3)
"""


@pytest.mark.parametrize("workers", [0, 2], ids=["serial", "xdist"])
def test_a_planted_leaking_test_fails_the_trw_mcp_suite_at_session_end(workers: int, tmp_path: Path) -> None:
    """One real end-to-end demonstration, run from inside the package so the real conftest applies.

    Under xdist a worker's own exit status never reaches the controller, so the
    worker must hand its leaked pids over or the run exits 0 (C1, 2026-09-25).
    """
    package_root = Path(__file__).resolve().parents[1]
    probe = Path(__file__).resolve().parent / f"_leak_probe_{os.getpid()}.py"
    probe.write_text(_PLANTED_LEAK_TEST, encoding="utf-8")
    basetemp = tmp_path / "bt"
    home = basetemp / "home"
    env = {**os.environ, "HOME": str(home), "TRW_USER_DIR": str(home / ".trw"), "COLUMNS": "80"}
    try:
        result = subprocess.run(
            [
                *[sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--basetemp", str(basetemp), str(probe)],
                *(["-n", str(workers)] if workers else []),
            ],
            cwd=str(package_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        probe.unlink(missing_ok=True)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "1 passed" in output, output
    assert "FAIL: 1 leaked memory daemon(s)" in output, output


# ── C1 2026-09-25: a daemon a test auto-started is stopped before it publishes ──


_PLANTED_AUTO_START_TEST = """
import os
from pathlib import Path

from trw_memory.daemon import DaemonPaths
from trw_memory.daemon import client as daemon_client


def test_auto_starts_a_daemon_and_returns_before_it_publishes(tmp_path):
    memory = tmp_path / "user" / "memory"
    memory.mkdir(parents=True, mode=0o700)
    spawned = daemon_client.start_daemon_detached(DaemonPaths(user_memory_dir=memory))
    Path(os.environ["TRW_PLANTED_PID_FILE"]).write_text(str(spawned.pid))
    assert spawned.running()
"""


def test_a_daemon_auto_started_by_a_test_does_not_outlive_it(tmp_path: Path) -> None:
    """The per-test reaps look for a discovery file, which an auto-start publishes seconds late.

    Run from inside the package so the real conftest applies: the spawn recorder
    must stop the daemon when the test ends, published or not.
    """
    package_root = Path(__file__).resolve().parents[1]
    probe = Path(__file__).resolve().parent / f"_auto_start_probe_{os.getpid()}.py"
    probe.write_text(_PLANTED_AUTO_START_TEST, encoding="utf-8")
    pid_file = tmp_path / "planted.pid"
    basetemp = tmp_path / "bt"
    home = basetemp / "home"
    env = {
        **os.environ,
        "HOME": str(home),
        "TRW_USER_DIR": str(home / ".trw"),
        "TRW_PLANTED_PID_FILE": str(pid_file),
        "COLUMNS": "80",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--basetemp", str(basetemp), str(probe)],
            cwd=str(package_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        probe.unlink(missing_ok=True)
    output = result.stdout + result.stderr
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    else:
        alive = True
        os.kill(pid, 9)
    assert not alive, f"the auto-started daemon {pid} outlived its test\n{output}"
    assert result.returncode == 0, output
    assert "leaked memory daemon" not in output, output


def test_the_xdist_controller_collects_a_workers_survivors_as_well_as_its_leaks() -> None:
    """The controller fails on what a finished worker hands over: its leaks AND its sweep's survivors."""
    from types import SimpleNamespace

    from tests import conftest as conftest_mod

    config = SimpleNamespace(stash={})
    node = SimpleNamespace(
        config=config,
        workeroutput={conftest_mod._WORKER_LEAKS_KEY: [7], conftest_mod._WORKER_SURVIVORS_KEY: [9]},
    )

    conftest_mod.pytest_testnodedown(node, None)

    assert config.stash[conftest_mod._WORKER_LEAKS] == [7]
    assert config.stash[conftest_mod._WORKER_SURVIVORS] == [9]
