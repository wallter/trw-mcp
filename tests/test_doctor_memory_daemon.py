"""PRD-CORE-253 FR03 — the ``doctor`` row for the loopback memory daemon.

The row must PROBE and never start: a diagnostic that spawned a daemon would
report a healthy one every time, which is the exact failure mode a reachability
check exists to catch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("trw_memory.daemon")

from trw_memory.daemon import DaemonInfo, DaemonPaths


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path / "userhome"


def _publish(paths: DaemonPaths, pid: int) -> DaemonInfo:
    paths.user_memory_dir.mkdir(parents=True, exist_ok=True)
    info = DaemonInfo(
        pid=pid,
        url="http://127.0.0.1:45678/mcp",
        token="never-printed",
        started_at="2026-09-03T00:00:00+00:00",
        version="0.16.0",
    )
    paths.discovery.write_text(info.model_dump_json(), encoding="utf-8")
    return info


def test_no_daemon_is_pass_not_warn(user_dir: Path) -> None:
    """Nothing attaches to the daemon yet, so "not running" is healthy.

    Reporting it as WARN would make a permanent warning out of correct
    behaviour and train an operator to ignore the row.
    """
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    status, message = memory_daemon_row()

    assert status == "PASS"
    assert "trw-memory-server serve http" in message
    assert str(user_dir / "memory") in message


def test_the_no_daemon_remedy_does_not_promise_an_auto_start(user_dir: Path) -> None:
    """The row must not tell the operator a client will start the daemon for it.

    No shipped client does: ``trw-mcp`` reads and writes its store directly, and
    the only ``DaemonClient`` caller in the tree is ``trw_memory.cli_namespace``
    (client attach is PRD-CORE-253 Slice B, deferred). The retired message said
    "the next store or recall starts one", so an operator who read the row and
    then stored a learning would believe a daemon had come up. The manual start
    command is the whole remedy, and both the row and the module docstring that
    explains it have to say so.
    """
    import re

    import trw_mcp.server._doctor_memory_daemon as row_module
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    _status, message = memory_daemon_row()

    assert "the next store or recall starts one" not in message
    assert "manually" in message, "the operator must be told the start is theirs to do"
    assert "trw-memory-server serve http" in message

    # The docstring is the other half of the same claim: a maintainer who trusts
    # it would put the auto-start promise straight back into the message.
    docstring = re.sub(r"\s+", " ", row_module.__doc__ or "")
    assert "a client auto-starts one on first need" not in docstring


def test_row_reports_pid_uptime_and_store_for_a_live_daemon(user_dir: Path) -> None:
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    info = _publish(DaemonPaths.resolve(), os.getpid())

    status, message = memory_daemon_row()

    assert status == "PASS"
    assert str(info.pid) in message
    assert info.url in message
    assert "up " in message
    assert str(user_dir / "memory") in message
    assert info.token not in message, "the token must never reach a diagnostic surface"


def test_a_record_naming_a_dead_process_warns_with_the_remedy(user_dir: Path) -> None:
    """THAT is the fault: a client reads the file, dials a dead port, fails closed."""
    import subprocess
    import sys

    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)
    paths = DaemonPaths.resolve()
    _publish(paths, dead.pid)

    status, message = memory_daemon_row()

    assert status == "WARN"
    assert str(paths.discovery) in message
    assert str(dead.pid) in message


def test_a_corrupt_record_warns_naming_the_file_and_reason(user_dir: Path) -> None:
    """DiscoveryInvalid is not "no daemon": fold it in and a second writer can bind.

    The record says nothing about whether a daemon is serving, so liveness
    cannot be assessed -- the message reports that explicitly rather than
    guessing PASS (absent) or a dead-process WARN.
    """
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    paths = DaemonPaths.resolve()
    paths.user_memory_dir.mkdir(parents=True, exist_ok=True)
    paths.discovery.write_text("{not valid json", encoding="utf-8")
    paths.discovery.chmod(0o600)

    status, message = memory_daemon_row()

    assert status == "WARN"
    assert str(paths.discovery) in message
    assert "not_measured" in message


def test_the_row_never_starts_a_daemon(user_dir: Path) -> None:
    """The whole point of a probe: no discovery file, no token, no process."""
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    memory_daemon_row()

    paths = DaemonPaths.resolve(create=False)
    assert not paths.discovery.exists()
    assert not paths.token.exists()


def test_the_row_is_registered_in_the_doctor_catalogue_and_json(user_dir: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Registered, not merely importable — an unwired check reports nothing."""
    import argparse

    from trw_mcp.server import _subcommands_doctor as doctor

    assert ("memory_daemon", "_check_memory_daemon") in doctor._CHECKS

    with pytest.raises(SystemExit) as exit_info:
        doctor._run_doctor(argparse.Namespace(target_dir=str(user_dir), format="json"))
    assert exit_info.value.code in (0, 1)

    payload = json.loads(capsys.readouterr().out)
    rows = {row["name"]: row for row in payload["checks"]}
    assert "memory_daemon" in rows, "the check is defined but never runs"
    assert rows["memory_daemon"]["status"] == "PASS"
