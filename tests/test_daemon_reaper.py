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
