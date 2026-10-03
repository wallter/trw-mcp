"""DAEMON-LEAK-INTERMITTENT: a daemon a test starts in the worker's session home fails THAT test, named.

The end-to-end case is ``test_ki1_r7_guarded_copy.py::test_c4_ledger_and_later_update`` (fixed: it undid the
autouse HOME redirect with ``monkeypatch.undo()``); these pin the detection and the conftest wiring.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._daemon_escape import escape_message, published_daemon_pids, session_homes, stop_escaped


def _publish(home: Path, pid: int, start: str | None = None) -> Path:
    path = home / ".trw-user" / "memory" / "daemon.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {"schema_version": 1, "pid": pid}
    if start is not None:
        record["process_start"] = start
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_only_a_daemon_published_during_the_test_counts_and_is_stopped(tmp_path: Path) -> None:
    old_home, new_home = tmp_path / "trw-session-home0", tmp_path / "trw-session-home1"
    _publish(old_home, 999_999_991)  # already there when the test began: not this test's
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        before = published_daemon_pids(session_homes(tmp_path))
        _publish(new_home, child.pid)
        after = published_daemon_pids(session_homes(tmp_path))

        escaped = stop_escaped(before, after)

        assert escaped == [f"pid {child.pid} ({new_home / '.trw-user' / 'memory' / 'daemon.json'})"]
        with pytest.raises(ProcessLookupError):  # stopped and reaped: the pid is gone
            os.kill(child.pid, 0)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_an_unreadable_or_absent_discovery_file_publishes_nothing(tmp_path: Path) -> None:
    broken = tmp_path / "trw-session-home0" / ".trw-user" / "memory" / "daemon.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{half-written", encoding="utf-8")

    assert published_daemon_pids([*session_homes(tmp_path), tmp_path / "missing-home"]) == {}


def test_the_failure_names_the_cause_and_the_remedy() -> None:
    message = escape_message(["pid 7 (/x/daemon.json)"])

    assert "pid 7" in message and "monkeypatch.undo()" in message and "monkeypatch.context()" in message


def test_the_per_test_reap_checks_the_session_home_and_fails_the_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring: the conftest reap snapshots the session home before the test and fails it on a new daemon after."""
    import tests.conftest as conftest

    source = inspect.getsource(conftest._reap_isolated_home_daemons)
    assert source.index("published_daemon_pids(") < source.index("yield")
    assert "stop_escaped(" in source and "pytest.fail(" in source


def test_a_reused_pid_is_never_signalled(tmp_path: Path) -> None:
    """A discovery file whose recorded start does not match the live pid's names a process that is not that daemon."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _publish(tmp_path / "trw-session-home0", child.pid, start="darwin:1.0")  # not this process's start

        escaped = stop_escaped({}, published_daemon_pids(session_homes(tmp_path)))

        assert escaped == []
        assert child.poll() is None, "an unrelated process that reused the pid must not be signalled"
    finally:
        child.kill()
        child.wait()


def test_the_discovery_read_survives_a_test_that_patched_path_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some tests patch Path.read_text / open to fail; the per-test check reads with os.read and still works."""
    _publish(tmp_path / "trw-session-home0", 4242)

    def boom(*_a: object, **_k: object) -> str:
        raise AssertionError("read_text")

    with monkeypatch.context() as mp:  # scoped: other teardowns still need Path.read_text
        mp.setattr(Path, "read_text", boom)
        found = published_daemon_pids(session_homes(tmp_path))

    assert list(found) == [4242]
