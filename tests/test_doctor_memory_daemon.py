"""PRD-CORE-253 FR03, PRD-CORE-310 FR05 — the ``doctor`` row for the loopback memory daemon.

The row must PROBE and never start: a diagnostic that spawned a daemon would
report a healthy one every time, which is the exact failure mode a reachability
check exists to catch. And liveness is the endpoint answering, not a pid: a live
process that serves nothing is the state every client fails in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("trw_memory.daemon")

from trw_memory.daemon import DaemonInfo, DaemonPaths

from tests._memory_fixtures import MemoryDaemon, attach_checkout

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("MEMORY_DAEMON_AUTOSTART", raising=False)  # these rows describe the default: auto-start on
    return tmp_path / "userhome"


def _publish(paths: DaemonPaths, pid: int) -> DaemonInfo:
    paths.user_memory_dir.mkdir(parents=True, exist_ok=True)
    info = DaemonInfo(
        pid=pid,
        url="http://127.0.0.1:45678/mcp",
        started_at="2026-09-03T00:00:00+00:00",
        version="0.16.0",
    )
    paths.discovery.write_text(info.model_dump_json(), encoding="utf-8")
    return info


def test_no_daemon_is_pass_not_warn(user_dir: Path) -> None:
    """The daemon starts on first memory use and exits when idle, so "not running" is healthy.

    Reporting it as WARN would make a permanent warning out of correct
    behaviour and train an operator to ignore the row.
    """
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    status, message = memory_daemon_row(user_dir)

    assert status == "PASS"
    assert "trw-memory-server serve http" in message
    assert str(user_dir / "memory") in message


def test_no_daemon_with_autostart_off_is_not_a_pass_that_promises_a_start(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INC-117(g): with auto-start off no call starts a daemon, so "the next memory call starts one" is false.

    ``memory_backend`` already reports the same state as unreachable; the two rows must not disagree.
    """
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")

    status, message = memory_daemon_row(user_dir)

    assert status == "WARN"
    assert "auto-start is off" in message
    assert "the next memory call starts one" not in message
    assert "trw-memory-server serve http" in message, "the manual start is the remedy"


def test_the_no_daemon_row_says_the_next_memory_call_starts_one(user_dir: Path) -> None:
    """The row and its docstring must match the client: ``DaemonClient._attach`` auto-starts the daemon.

    The retired wording ("nothing starts one for you ... client attach is a
    planned slice") predated the client auto-start and told an operator to start
    by hand a daemon their next ``trw_learn`` would have started anyway.
    """
    import inspect
    import re

    from trw_memory.daemon.client import DaemonClient

    import trw_mcp.server._doctor_memory_daemon as row_module
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    _status, message = memory_daemon_row(user_dir)

    assert "the next memory call starts one" in message
    assert "nothing starts one" not in message
    assert "planned slice" not in message
    assert "trw-memory-server serve http" in message, "the manual start stays available as a remedy"

    docstring = re.sub(r"\s+", " ", row_module.__doc__ or "")
    assert "planned slice" not in docstring
    # The claim holds only while the client really does start one on attach.
    assert "start_daemon_detached" in inspect.getsource(DaemonClient._attach)


@pytest.mark.parametrize("granted", [True, False], ids=["checkout-grant", "no-grant"])
def test_row_passes_a_daemon_only_once_its_endpoint_answers(
    memory_daemon: MemoryDaemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, granted: bool
) -> None:
    """The ping carries the checkout's grant; without one, the daemon's 401 still proves it serves."""
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    checkout = tmp_path / "repo"
    if granted:
        attach_checkout(checkout / ".trw", memory_daemon)
    info = DaemonPaths.resolve().discovery.read_text(encoding="utf-8")

    status, message = memory_daemon_row(checkout)

    assert status == "PASS", message
    assert str(DaemonInfo.model_validate_json(info).pid) in message
    assert "answered" in message
    assert "up " in message
    assert str(memory_daemon.paths.user_memory_dir) in message


def test_a_live_process_whose_endpoint_does_not_answer_fails_naming_it(user_dir: Path) -> None:
    """PRD-CORE-310 FR05: pre-change this was PASS, because a pid check found the process alive."""
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    info = _publish(DaemonPaths.resolve(), os.getpid())

    status, message = memory_daemon_row(user_dir)

    assert status == "FAIL"
    assert f"process {info.pid}" in message and "report this to the user" in message
    assert info.url in message


def test_a_record_naming_a_dead_process_warns_that_the_next_call_replaces_it(user_dir: Path) -> None:
    """A crash is worth seeing, but nothing is stuck: clients replace a dead record (trw-memory 4.0.0)."""
    import subprocess
    import sys

    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)
    paths = DaemonPaths.resolve()
    _publish(paths, dead.pid)

    status, message = memory_daemon_row(user_dir)

    assert status == "WARN"
    assert str(paths.discovery) in message
    assert str(dead.pid) in message
    assert "next memory call replaces the record" in message
    assert "fail closed" not in message


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

    status, message = memory_daemon_row(user_dir)

    assert status == "WARN"
    assert str(paths.discovery) in message
    assert "not_measured" in message


def test_the_row_never_starts_a_daemon(user_dir: Path) -> None:
    """The whole point of a probe: no discovery file, no grant, no process."""
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    memory_daemon_row(user_dir)

    paths = DaemonPaths.resolve(create=False)
    assert not paths.discovery.exists()
    assert not paths.grants.exists()


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


def _answering_daemon(user_dir: Path, monkeypatch: pytest.MonkeyPatch, *, version: str) -> DaemonInfo:
    """A live, answering daemon record reporting *version*, without starting a process."""
    import trw_memory.daemon as daemon_pkg
    import trw_memory.daemon.client as daemon_client

    info = _publish(DaemonPaths.resolve(), os.getpid()).model_copy(update={"version": version})

    async def _answered(*_args: object, **_kwargs: object) -> str:
        return "answered"

    monkeypatch.setattr(daemon_pkg, "read_discovery_result", lambda _paths: info)
    monkeypatch.setattr(DaemonInfo, "is_live", lambda _self, _lock: True)
    monkeypatch.setattr(daemon_client, "probe_endpoint", _answered)
    return info


def test_an_answering_daemon_on_another_trw_memory_version_warns_with_the_stop_remedy(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upgrade leaves the old daemon serving old code (a 7.0.1 record cannot be stopped automatically).

    A PASS there turned the upgrade's verify step green over a stale daemon; the row must say so
    and name how to stop it.
    """
    import trw_memory

    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    stale = "0.0.0-stale"  # never the installed version, whatever release this runs under
    assert stale != trw_memory.__version__
    info = _answering_daemon(user_dir, monkeypatch, version=stale)

    status, message = memory_daemon_row(user_dir)

    assert status == "WARN", message
    assert stale in message and trw_memory.__version__ in message
    assert f"process {info.pid}" in message and "report this to the user" in message
    assert "next memory call starts" in message


def test_an_answering_daemon_on_this_trw_memory_version_still_passes(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_memory

    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    _answering_daemon(user_dir, monkeypatch, version=trw_memory.__version__)

    status, message = memory_daemon_row(user_dir)

    assert status == "PASS", message
    assert f"version {trw_memory.__version__}" in message
