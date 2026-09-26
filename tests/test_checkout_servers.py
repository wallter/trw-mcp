"""The trw-mcp stdio servers still running against a checkout, from its pins.json.

``memory migrate`` and the installer print these so the user knows which
clients to reconnect: a server started before an upgrade runs the old code, and
an old server keeps writing the checkout store the migration just emptied.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state._checkout_servers import live_servers, stray_servers
from trw_mcp.state._process_identity import read_process_start_time

pytestmark = pytest.mark.unit


@pytest.fixture
def server() -> Iterator[subprocess.Popen[bytes]]:
    """A live child process standing in for a trw-mcp server this test process launched."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _write_pins(trw_dir: Path, records: dict[str, dict[str, object]]) -> None:
    (trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    (trw_dir / "runtime" / "pins.json").write_text(json.dumps(records), encoding="utf-8")


def _record(pid: int, client_pid: int, client_start: str | None, created: datetime) -> dict[str, object]:
    return {
        "run_path": "/r",
        "created_ts": _iso(created),
        "last_heartbeat_ts": _iso(created),
        "pid": pid,
        "client_pid": client_pid,
        "client_start": client_start,
    }


def test_a_live_server_is_named_with_the_client_that_launched_it(
    tmp_path: Path, server: subprocess.Popen[bytes]
) -> None:
    me = os.getpid()
    _write_pins(tmp_path, {"s": _record(server.pid, me, read_process_start_time(me), datetime.now(timezone.utc))})

    (line,) = live_servers(tmp_path)

    assert f"trw-mcp pid {server.pid}" in line
    assert f"(pid {me})" in line
    assert "python" in line.lower(), line


def test_a_server_whose_client_exited_is_reported_as_orphaned(tmp_path: Path, server: subprocess.Popen[bytes]) -> None:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    _write_pins(tmp_path, {"s": _record(server.pid, gone.pid, "1", datetime.now(timezone.utc))})

    (line,) = live_servers(tmp_path)

    assert "orphaned" in line
    assert f"kill {server.pid}" in line


def test_dead_and_recycled_servers_are_not_listed(tmp_path: Path, server: subprocess.Popen[bytes]) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    me = os.getpid()
    now = datetime.now(timezone.utc)
    _write_pins(
        tmp_path,
        {
            "dead": _record(dead.pid, me, read_process_start_time(me), now),
            # Recorded an hour before this pid's process started: the pid was reused.
            "recycled": _record(server.pid, me, read_process_start_time(me), now - timedelta(hours=1)),
        },
    )

    assert live_servers(tmp_path) == []


@pytest.mark.parametrize("content", [None, "not json", "[1, 2]"], ids=["absent", "malformed", "not-a-map"])
def test_no_readable_pins_means_no_servers_listed(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / "runtime").mkdir()
        (tmp_path / "runtime" / "pins.json").write_text(content, encoding="utf-8")

    assert live_servers(tmp_path) == []


def test_stray_servers_are_orphans_and_every_server_but_the_newest_under_one_client(
    tmp_path: Path, server: subprocess.Popen[bytes]
) -> None:
    newer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    try:
        me, now = os.getpid(), datetime.now(timezone.utc)
        mine = read_process_start_time(me)
        _write_pins(
            tmp_path,
            {
                "old": _record(server.pid, me, mine, now),
                "new": _record(newer.pid, me, mine, now + timedelta(seconds=1)),
                "orphan": _record(orphan.pid, gone.pid, "1", now),
            },
        )

        lines = stray_servers(tmp_path)

        assert len(lines) == 2, lines
        assert any(f"trw-mcp pid {server.pid}" in line and "superseded" in line for line in lines)
        assert any(f"trw-mcp pid {orphan.pid}" in line and "orphaned" in line for line in lines)
        assert not any(f"trw-mcp pid {newer.pid}" in line for line in lines)
    finally:
        for proc in (newer, orphan):
            proc.kill()
            proc.wait()


def test_one_server_per_live_client_is_not_stray(tmp_path: Path, server: subprocess.Popen[bytes]) -> None:
    me = os.getpid()
    _write_pins(tmp_path, {"s": _record(server.pid, me, read_process_start_time(me), datetime.now(timezone.utc))})

    assert stray_servers(tmp_path) == []
