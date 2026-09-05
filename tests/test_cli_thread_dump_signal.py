"""``trw-mcp`` registers a SIGUSR1 all-thread stack dump at boot (no ptrace needed)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap

import pytest

from trw_mcp.server._cli import _register_thread_dump_signal


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="platform has no SIGUSR1")
def test_register_thread_dump_signal_reports_true_on_posix() -> None:
    assert _register_thread_dump_signal() is True


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="platform has no SIGUSR1")
def test_sigusr1_dumps_every_thread_without_killing_the_process(tmp_path: Path) -> None:
    """No resolvable .trw in cwd -> the dump falls back to stderr (run from an empty dir)."""
    """The default SIGUSR1 action terminates the process; after registration it dumps and keeps serving."""
    script = textwrap.dedent(
        """
        import os, signal, sys, threading, time
        from trw_mcp.server._cli import _register_thread_dump_signal
        assert _register_thread_dump_signal()
        stop = threading.Event()
        threading.Thread(target=stop.wait, name="probe-worker", daemon=True).start()
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.5)
        print("still-alive", flush=True)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=60, env={**os.environ}
    )
    assert proc.returncode == 0, proc.stderr
    assert "still-alive" in proc.stdout
    assert proc.stderr.count("hread 0x") >= 2, proc.stderr  # "Current thread" (main) + "Thread" (probe-worker)
    assert "stop.wait" in proc.stderr or "wait" in proc.stderr


@pytest.mark.unit
def test_sigusr1_dump_lands_in_the_project_log_file(tmp_path: Path) -> None:
    """With a resolvable dump dir the stacks go to a file an operator can read later."""
    dump_dir = tmp_path / ".trw" / "logs"
    script = (
        "import os, signal, sys, threading, time\n"
        "from pathlib import Path\n"
        "from trw_mcp.server._cli import _register_thread_dump_signal\n"
        f"assert _register_thread_dump_signal(Path({str(dump_dir)!r}))\n"
        "stop = threading.Event()\n"
        "threading.Thread(target=stop.wait, name='probe-worker', daemon=True).start()\n"
        "os.kill(os.getpid(), signal.SIGUSR1)\n"
        "time.sleep(0.5)\n"
        "stop.set()\n"
        "print('alive')\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "alive" in proc.stdout
    files = list(dump_dir.glob("thread-dump-*.txt"))
    assert len(files) == 1, proc.stderr
    body = files[0].read_text(encoding="utf-8")
    assert body.count("hread 0x") >= 2, body
    assert "thread dumps ->" in proc.stderr @ pytest.mark.unit


def test_sigusr1_dumps_every_thread_without_killing_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """With no resolvable ``.trw`` in cwd the dump falls back to stderr and the process survives."""
    import os
    import signal
    import threading
    import time

    monkeypatch.chdir(tmp_path)  # a fresh, empty cwd: no .trw here
    assert _register_thread_dump_signal() is True
    stop = threading.Event()
    threading.Thread(target=stop.wait, name="probe-worker", daemon=True).start()
    os.kill(os.getpid(), signal.SIGUSR1)
    time.sleep(0.5)
    stop.set()
    err = capfd.readouterr().err
    assert err.count("hread 0x") >= 2, err  # "Current thread" (main) + "Thread" (probe-worker)
    assert "wait" in err, err
    assert not (tmp_path / ".trw").exists()


@pytest.mark.unit
def test_sigusr1_dump_lands_in_the_project_log_file(tmp_path: Path) -> None:
    """With a resolvable dump dir the stacks go to a file an operator can read later."""
    dump_dir = tmp_path / ".trw" / "logs"
    script = (
        "import os, signal, sys, threading, time\n"
        "from pathlib import Path\n"
        "from trw_mcp.server._cli import _register_thread_dump_signal\n"
        f"assert _register_thread_dump_signal(Path({str(dump_dir)!r}))\n"
        "stop = threading.Event()\n"
        "threading.Thread(target=stop.wait, name='probe-worker', daemon=True).start()\n"
        "os.kill(os.getpid(), signal.SIGUSR1)\n"
        "time.sleep(0.5)\n"
        "stop.set()\n"
        "print('alive')\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "alive" in proc.stdout
    files = list(dump_dir.glob("thread-dump-*.txt"))
    assert len(files) == 1, proc.stderr
    body = files[0].read_text(encoding="utf-8")
    assert body.count("hread 0x") >= 2, body
    assert "thread dumps ->" in proc.stderr
