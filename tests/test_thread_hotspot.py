"""``own_thread_hotspot``: a server's own hottest-thread CPU share, fed into
``trw_checkpoint(heartbeat=True)`` (a checkpoint mode since PRD-CORE-300 S6a,
formerly a standalone heartbeat tool).

Measured incident (PRD-FIX-131 follow-up): live trw-mcp servers with ONE worker
thread at 70-85% CPU for hours, and ``py-spy``/``gdb`` unavailable. The server
reads its own ``/proc`` entries; these tests point that read at a fake tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state import _thread_hotspot as thread_hotspot
from trw_mcp.state._thread_hotspot import own_thread_hotspot


def _stat_line(pid: int, *, utime: int, stime: int, starttime: int, comm: str = "python") -> str:
    """Build a minimal ``/proc/.../stat`` line with fields 3..22 populated.

    Field 2 (``comm``) is parenthesised; the reader anchors on the LAST ``)``
    so a real ``comm`` containing spaces would not break parsing -- this
    fixture just uses a plain name.
    """
    fields = ["0"] * 20
    fields[0] = "S"  # field 3: state
    fields[11] = str(utime)  # field 14: utime
    fields[12] = str(stime)  # field 15: stime
    fields[19] = str(starttime)  # field 22: starttime
    return f"{pid} ({comm}) " + " ".join(fields)


def _write_process(
    proc_root: Path,
    pid: int,
    *,
    starttime_ticks: int,
    threads: dict[int, tuple[int, int]],
) -> None:
    """Write ``<proc_root>/<pid>/{stat,cmdline,task/<tid>/stat}`` for one fake server.

    *threads* maps tid -> (utime, stime) in clock ticks.
    """
    pid_dir = proc_root / str(pid)
    pid_dir.mkdir(parents=True)
    (pid_dir / "stat").write_text(_stat_line(pid, utime=0, stime=0, starttime=starttime_ticks), encoding="utf-8")
    (pid_dir / "cmdline").write_bytes(b"trw-mcp\x00serve\x00--transport\x00stdio\x00")
    task_dir = pid_dir / "task"
    task_dir.mkdir()
    for tid, (utime, stime) in threads.items():
        tid_dir = task_dir / str(tid)
        tid_dir.mkdir()
        (tid_dir / "stat").write_text(_stat_line(tid, utime=utime, stime=stime, starttime=0), encoding="utf-8")


def _fake_proc(tmp_path: Path, *, sys_uptime: float) -> Path:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "uptime").write_text(f"{sys_uptime} 0.00\n", encoding="utf-8")
    return proc_root


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic clock ticks and a Linux platform."""
    monkeypatch.setattr(thread_hotspot, "_clk_tck", lambda: 100.0)
    monkeypatch.setattr(thread_hotspot.sys, "platform", "linux")


def test_own_thread_hotspot_none_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(thread_hotspot.sys, "platform", "darwin")
    assert own_thread_hotspot() is None


def test_own_thread_hotspot_measures_the_calling_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    monkeypatch.setattr(thread_hotspot.sys, "platform", "linux")
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(thread_hotspot, "_PROC_ROOT", proc_root)
    own_pid = os.getpid()
    _write_process(proc_root, own_pid, starttime_ticks=0, threads={1: (60000, 0)})  # 600s / 1000s = 60%

    result = own_thread_hotspot()

    assert result is not None
    assert result["share"] == pytest.approx(0.6, abs=1e-6)
    assert result["cpu_seconds"] == pytest.approx(600.0, abs=1e-6)


def test_own_thread_hotspot_none_when_unmeasurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(thread_hotspot.sys, "platform", "linux")
    monkeypatch.setattr(thread_hotspot, "_PROC_ROOT", tmp_path / "no-such-proc")
    assert own_thread_hotspot() is None


def test_own_thread_hotspot_reports_the_hottest_of_several_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(thread_hotspot, "_PROC_ROOT", proc_root)
    # tid 2 dominates: 900s of 1000s uptime.
    _write_process(proc_root, os.getpid(), starttime_ticks=0, threads={1: (5000, 0), 2: (90000, 0), 3: (100, 0)})

    assert own_thread_hotspot() == {"share": 0.9, "cpu_seconds": 900.0}
