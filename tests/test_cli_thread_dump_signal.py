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
def test_sigusr1_dumps_every_thread_without_killing_the_process() -> None:
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
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env={**os.environ}
    )
    assert proc.returncode == 0, proc.stderr
    assert "still-alive" in proc.stdout
    assert proc.stderr.count("hread 0x") >= 2, proc.stderr  # "Current thread" (main) + "Thread" (probe-worker)
    assert "stop.wait" in proc.stderr or "wait" in proc.stderr
