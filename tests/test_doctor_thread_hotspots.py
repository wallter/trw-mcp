"""``trw-mcp doctor`` thread-hotspots row (PRD-FIX-131 operator-visibility follow-up).

Before this row, an operator who suspected a spinning worker thread (measured
incident: three live trw-mcp servers at 70-85% CPU on one thread for hours,
with ``py-spy``/``gdb`` unavailable — ``ptrace_scope=1``, no sudo) had no way
to discover WHICH pid to signal without already knowing it. The row must be
exercised through the real doctor catalogue for at least one case, so a check
that exists but is never registered fails here (the same pattern
``test_doctor_memory_daemon.py``/``test_doctor_wal_line.py`` use).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.server import _doctor_thread_hotspots as doctor_thread_hotspots
from trw_mcp.server._doctor_thread_hotspots import own_thread_hotspot, thread_hotspot_row

_SHARE = 0.5
_MIN_SECONDS = 300.0


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


def _project(tmp_path: Path, *, pids: list[int]) -> Path:
    project = tmp_path / "project"
    writers_dir = project / ".trw" / "memory" / "memory.db.writers"
    writers_dir.mkdir(parents=True)
    for pid in pids:
        (writers_dir / f"{pid}.lock").write_text(f"{pid}\n1788583280.0\n", encoding="utf-8")
    return project


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic clock-ticks-per-second and always-alive pids for these tests."""
    monkeypatch.setattr(doctor_thread_hotspots, "_clk_tck", lambda: 100.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_pid_is_alive", lambda _pid: True)


@pytest.mark.unit
def test_skip_on_non_linux() -> None:
    status, message = thread_hotspot_row(
        Path("/nonexistent"), share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=False
    )
    assert status == "SKIP"
    assert status != "PASS"
    assert "NOT MEASURED" in message


def test_skip_when_proc_uptime_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable ``/proc/uptime`` (or non-Linux /proc) must SKIP, never PASS."""
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", tmp_path / "no-such-proc")

    status, message = thread_hotspot_row(tmp_path, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "SKIP"
    assert status != "PASS"
    assert "NOT MEASURED" in message


def test_pass_when_no_live_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    project = _project(tmp_path, pids=[])

    status, message = thread_hotspot_row(project, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "PASS"
    assert "no live trw-mcp server" in message


def test_dead_pid_is_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock naming a pid that is no longer alive must not be reported at all."""
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    monkeypatch.setattr(doctor_thread_hotspots, "_pid_is_alive", lambda _pid: False)
    project = _project(tmp_path, pids=[999001])

    status, message = thread_hotspot_row(project, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "PASS"
    assert "999001" not in message


def test_pass_when_hottest_thread_below_both_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    # clk_tck=100 -> uptime = 1000 - 0/100 = 1000s; hottest thread cpu = 200/100 = 200s (20% share).
    _write_process(proc_root, 424242, starttime_ticks=0, threads={1: (20000, 0)})
    project = _project(tmp_path, pids=[424242])

    status, message = thread_hotspot_row(project, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "PASS"
    assert "424242" in message
    assert "20%" in message


def test_pass_when_share_exceeded_but_below_min_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    """The AND: high share alone (58%) must not WARN when cpu seconds (290s) misses the floor (300s)."""
    proc_root = _fake_proc(tmp_path, sys_uptime=500.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    _write_process(proc_root, 424243, starttime_ticks=0, threads={1: (29000, 0)})  # 290s / 500s = 58%
    project = _project(tmp_path, pids=[424243])

    status, message = thread_hotspot_row(project, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "PASS"
    assert "424243" in message


def test_warn_when_both_thresholds_are_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    # cpu = 90000/100 = 900s; share = 900/1000 = 90% -- clears both 300s and 50%.
    _write_process(proc_root, 424244, starttime_ticks=0, threads={7: (90000, 0)})
    project = _project(tmp_path, pids=[424244])

    status, message = thread_hotspot_row(project, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "WARN"
    assert "424244" in message
    assert "tid 7" in message
    assert "kill -USR1 424244" in message
    assert str(project / ".trw" / "logs" / "thread-dump-424244.txt") in message


def test_hottest_thread_is_the_max_across_multiple_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    _write_process(
        proc_root,
        424245,
        starttime_ticks=0,
        threads={1: (5000, 0), 2: (90000, 0), 3: (100, 0)},  # tid 2 dominates
    )
    project = _project(tmp_path, pids=[424245])

    status, message = thread_hotspot_row(project, share_threshold=_SHARE, min_seconds=_MIN_SECONDS, is_linux=True)

    assert status == "WARN"
    assert "tid 2" in message
    assert "tid 1" not in message
    assert "tid 3" not in message


# -- own_thread_hotspot() (fed into trw_heartbeat) ----------------------------


def test_own_thread_hotspot_none_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_thread_hotspots.sys, "platform", "darwin")
    assert own_thread_hotspot() is None


def test_own_thread_hotspot_measures_the_calling_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patched: None
) -> None:
    import os

    monkeypatch.setattr(doctor_thread_hotspots.sys, "platform", "linux")
    proc_root = _fake_proc(tmp_path, sys_uptime=1000.0)
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", proc_root)
    own_pid = os.getpid()
    _write_process(proc_root, own_pid, starttime_ticks=0, threads={1: (60000, 0)})  # 600s / 1000s = 60%

    result = own_thread_hotspot()

    assert result is not None
    assert result["share"] == pytest.approx(0.6, abs=1e-6)
    assert result["cpu_seconds"] == pytest.approx(600.0, abs=1e-6)


def test_own_thread_hotspot_none_when_unmeasurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_thread_hotspots.sys, "platform", "linux")
    monkeypatch.setattr(doctor_thread_hotspots, "_PROC_ROOT", tmp_path / "no-such-proc")
    assert own_thread_hotspot() is None


# -- Doctor catalogue registration --------------------------------------------


def test_thread_hotspots_registered_in_the_doctor_catalogue(tmp_path: Path) -> None:
    """Registered, not merely importable -- an unwired check reports nothing."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_doctor as doctor

    assert ("thread_hotspots", "_check_thread_hotspots") in doctor._CHECKS

    results = doctor._doctor_core(tmp_path, TRWConfig())
    matches = [r for r in results if r.name == "thread_hotspots"]
    assert matches, f"doctor produced no thread_hotspots row; got {[r.name for r in results]}"
    # A tmp_path with no .trw/memory/memory.db.writers is the ordinary "no live
    # server" PASS on a real Linux box running this suite; SKIP is acceptable
    # only if this box's own /proc genuinely cannot be read.
    assert matches[0].status in {"PASS", "WARN", "SKIP"}
