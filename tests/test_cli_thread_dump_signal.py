"""``trw-mcp serve`` answers SIGUSR1 with an all-thread stack dump (no ptrace needed).

The handler is registered on the serve path only and opens its file only when
the signal arrives, so no other command -- ``--help``, ``local recall``, every
hook that shells out -- leaves a ``thread-dump-<pid>.txt`` or a stderr line.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_NO_SIGUSR1 = pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="platform has no SIGUSR1")


def _project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "proj"
    (root / ".trw").mkdir(parents=True)
    env = {**os.environ, "HOME": str(tmp_path / "home"), "TRW_USER_DIR": str(tmp_path / "userhome")}
    return root, env


def _dumps(root: Path) -> list[Path]:
    return sorted((root / ".trw" / "logs").glob("thread-dump-*.txt"))


@_NO_SIGUSR1
@pytest.mark.parametrize("argv", [["--help"], ["local", "recall", "--query", "anything"]])
def test_a_command_other_than_serve_leaves_no_thread_dump_file_or_line(tmp_path: Path, argv: list[str]) -> None:
    root, env = _project(tmp_path)

    done = subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", *argv], cwd=root, env=env, capture_output=True, text=True, timeout=120
    )

    assert _dumps(root) == [], done.stderr
    assert "SIGUSR1" not in done.stderr and "thread dump" not in done.stderr


@_NO_SIGUSR1
def test_serve_writes_a_dump_only_when_signalled_and_keeps_serving(tmp_path: Path) -> None:
    from trw_mcp.state._hook_flags import hook_flags_path

    root, env = _project(tmp_path)
    server = subprocess.Popen(
        [sys.executable, "-m", "trw_mcp.server", "serve"],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # The handler is registered just before the hook flags are published.
        deadline = time.monotonic() + 60
        while not hook_flags_path(root / ".trw").is_file():
            assert server.poll() is None and time.monotonic() < deadline, "serve did not boot"
            time.sleep(0.1)
        assert _dumps(root) == [], "a server that is never signalled leaves no file"

        server.send_signal(signal.SIGUSR1)
        while not (_dumps(root) and _dumps(root)[0].stat().st_size):
            assert server.poll() is None, "SIGUSR1 killed the server"
            assert time.monotonic() < deadline, "no dump was written"
            time.sleep(0.1)

        (dump,) = _dumps(root)
        assert dump.name == f"thread-dump-{server.pid}.txt"
        assert "hread 0x" in dump.read_text(encoding="utf-8")
        assert server.poll() is None, "the server keeps serving after the dump"
    finally:
        server.kill()
        _, err = server.communicate(timeout=30)
    assert "SIGUSR1" not in err


@_NO_SIGUSR1
def test_the_dump_falls_back_to_stderr_without_a_project(tmp_path: Path) -> None:
    script = (
        "import os, signal, threading, time\n"
        "from trw_mcp.server._cli import _register_thread_dump_signal\n"
        "assert _register_thread_dump_signal()\n"
        "stop = threading.Event()\n"
        "threading.Thread(target=stop.wait, name='probe-worker', daemon=True).start()\n"
        "os.kill(os.getpid(), signal.SIGUSR1)\n"
        "time.sleep(0.5)\n"
        "print('alive')\n"
    )

    done = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=60)

    assert done.returncode == 0 and "alive" in done.stdout, done.stderr
    assert done.stderr.count("hread 0x") >= 2, done.stderr  # the main thread and probe-worker
    assert not (tmp_path / ".trw").exists()
