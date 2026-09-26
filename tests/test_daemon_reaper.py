"""The session-end sweep finds daemons that never published a discovery file.

2026-09-24: five daemons from one test stalled before creating their memory
directory. No discovery file named them, so a sweep by discovery file could not
find them; the auto-started daemon's environment still places it under the run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._daemon_reaper import reap_daemons_under

pytestmark = pytest.mark.integration

_SLEEP = "import time; time.sleep(60)"


@pytest.fixture
def spawned() -> Iterator[list[subprocess.Popen[bytes]]]:
    processes: list[subprocess.Popen[bytes]] = []
    yield processes
    for process in processes:
        process.kill()
        process.wait()


def _unpublished(cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """A process whose command line is the daemon's and that never wrote ``daemon.json``."""
    return subprocess.Popen(
        [sys.executable, "-c", _SLEEP, "trw_memory.server", "serve", "http"], env={**os.environ, **env}, cwd=cwd
    )


@pytest.mark.parametrize("variable", ["TRW_USER_DIR", "HOME"])
def test_an_unpublished_daemon_placed_under_the_run_is_stopped(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]], variable: str
) -> None:
    root, elsewhere = tmp_path / "run", tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    daemon = _unpublished(elsewhere, {variable: str(root / "home" / ".trw")})
    spawned.append(daemon)

    assert reap_daemons_under(root, wait=True, by_process=True) == [daemon.pid]
    assert daemon.poll() is not None


def test_an_unpublished_daemon_running_in_the_run_is_stopped(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    daemon = _unpublished(root, {"TRW_USER_DIR": str(tmp_path / "outside"), "HOME": str(tmp_path / "outside")})
    spawned.append(daemon)

    assert reap_daemons_under(root, wait=True, by_process=True) == [daemon.pid]


def test_a_daemon_placed_and_running_elsewhere_is_left_alone(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    root, elsewhere = tmp_path / "run", tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    other = _unpublished(elsewhere, {"TRW_USER_DIR": str(elsewhere), "HOME": str(elsewhere)})
    spawned.append(other)

    assert reap_daemons_under(root, wait=True, by_process=True) == []
    assert other.poll() is None


def test_a_process_placed_under_the_run_that_is_not_a_daemon_is_never_signalled(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    root = tmp_path / "run"
    root.mkdir()
    bystander = subprocess.Popen([sys.executable, "-c", _SLEEP], env={**os.environ, "HOME": str(root)}, cwd=root)
    spawned.append(bystander)

    assert reap_daemons_under(root, wait=True, by_process=True) == []
    assert bystander.poll() is None


def test_a_narrow_columns_setting_does_not_hide_a_daemon(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linux ps cuts piped output to COLUMNS, and pytest sets COLUMNS: the command-line mark was lost (6.1.0 Linux leg)."""
    monkeypatch.setenv("COLUMNS", "20")
    root = tmp_path / "run"
    root.mkdir()
    daemon = _unpublished(root, {"TRW_USER_DIR": str(root / "home" / ".trw")})
    spawned.append(daemon)

    assert reap_daemons_under(root, wait=True, by_process=True) == [daemon.pid]


# ── PRD-INFRA-196-FR07: the suite itself fails when the session-end sweep found a leak ──


def test_pytest_sessionfinish_fails_the_session_when_the_sweep_reaped_a_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.conftest as conftest_mod

    class _FakeSession:
        exitstatus = 0

        class config:
            _tmp_path_factory = type("F", (), {"getbasetemp": staticmethod(lambda: Path("/tmp/x"))})()
            stash = pytest.Stash()

    monkeypatch.setattr(conftest_mod, "reap_daemons_under", lambda *_a, **_k: [12345])
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

    monkeypatch.setattr(conftest_mod, "reap_daemons_under", lambda *_a, **_k: [])
    monkeypatch.setattr(conftest_mod, "_timing_sessionfinish", lambda *_a, **_k: None)
    session = _FakeSession()

    conftest_mod.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert session.exitstatus == 0


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


def test_stop_spawned_stops_a_running_daemon_and_skips_an_exited_one(
    tmp_path: Path, spawned: list[subprocess.Popen[bytes]]
) -> None:
    from tests._daemon_reaper import stop_spawned

    running = _unpublished(tmp_path, {})
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    spawned.append(running)

    assert stop_spawned([running, exited]) == [running.pid]
    assert running.poll() is not None


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
    assert spawned.poll() is None
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
