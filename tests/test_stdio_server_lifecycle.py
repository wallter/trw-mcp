"""The stdio server exits when its client goes away (sprint-mcp7 W12).

A ``/mcp`` reconnect left old servers alive for a day; 6-11 orphans held the
checkout store and blocked ``memory migrate``. Two ways a client goes away:
it closes stdin (EOF), or it dies while something else still holds the pipe
open (the server is reparented and never sees EOF). Both must end the process.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._stdio_harness import StdioServerHarness, pid_is_live, stdio_import_skip_reason

_SKIP_REASON = stdio_import_skip_reason()

pytestmark = [
    pytest.mark.timeout(180),
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
    pytest.mark.skipif(sys.platform == "win32", reason="parent-loss is detected through POSIX reparenting"),
]

#: Upper bound for the process to be gone once its client is gone.
_EXIT_WITHIN_S = 10.0


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[StdioServerHarness]:
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    harness = StdioServerHarness(project, tmp_path / "user", tmp_path / "stderr")
    try:
        yield harness
    finally:
        harness.teardown()


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_is_live(pid):
            return True
        time.sleep(0.2)
    return False


def test_server_exits_on_stdin_eof_after_initialize(harness: StdioServerHarness) -> None:
    server, _ = harness.cold_initialize("eof")
    assert server.proc.stdin is not None
    server.proc.stdin.close()
    assert server.proc.wait(timeout=_EXIT_WITHIN_S) is not None


def test_server_exits_when_its_parent_dies_with_stdin_still_open(harness: StdioServerHarness) -> None:
    # We hold both pipe ends, so the server never sees EOF: only parent loss can end it.
    to_server_r, to_server_w = os.pipe()
    from_server_r, from_server_w = os.pipe()
    child = (
        "import subprocess, sys\n"
        f"p = subprocess.Popen([sys.executable, '-m', 'trw_mcp.server'], stdin={to_server_r}, "
        f"stdout={from_server_w}, stderr=subprocess.DEVNULL, pass_fds=({to_server_r}, {from_server_w}), "
        f"cwd={str(harness.project_root)!r}, env={harness.child_env('parent-loss')!r})\n"
        "print(p.pid, flush=True)\n"
        "import time; time.sleep(600)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", child], pass_fds=(to_server_r, from_server_w), stdout=subprocess.PIPE, text=True
    )
    server_pid = 0
    try:
        assert parent.stdout is not None
        server_pid = int(parent.stdout.readline())
        os.close(to_server_r)
        os.close(from_server_w)
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "w12", "version": "0"},
            },
        }
        os.write(to_server_w, json.dumps(request).encode() + b"\n")
        with os.fdopen(from_server_r, "rb", closefd=False) as replies:
            assert json.loads(replies.readline())["id"] == 1  # the server is up and serving

        parent.kill()
        parent.wait()

        assert _gone_within(server_pid, _EXIT_WITHIN_S), f"server pid {server_pid} outlived its parent"
    finally:
        if server_pid and pid_is_live(server_pid):
            os.kill(server_pid, signal.SIGKILL)
        if parent.poll() is None:
            parent.kill()
        os.close(to_server_w)
        os.close(from_server_r)
