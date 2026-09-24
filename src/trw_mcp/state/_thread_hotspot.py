"""This server's own hottest-thread CPU share, for ``trw_heartbeat`` (PRD-FIX-131 follow-up).

Incident this exists to surface (2026-09-05): three live trw-mcp stdio servers
each had ONE worker thread burning 70-85% of process CPU for hours while
``trw_deliver`` hung for 1,800s. ``py-spy``/``gdb`` need ptrace, so the only
zero-permission diagnostic is ``/proc`` itself. A server reads its OWN
``/proc/self``-equivalent entries, so there is no pid discovery and no chance of
measuring an unrelated process. The cross-process ``trw-mcp doctor`` row that
used to census other servers through the pin store was deleted with PRD-CORE-298
FR01 D1: it could not identify a pinned pid reliably and SKIPped off Linux.

Linux only; ``None`` everywhere else, never a fabricated zero.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["own_thread_hotspot"]

_PROC_ROOT = Path("/proc")  # patchable seam: tests monkeypatch this to a fake tree


def _read_text(path: Path) -> str | None:
    """Read *path* as text, or ``None`` when the read fails.

    A ``/proc`` entry vanishing mid-scan (its process or thread exited between
    discovery and read) or an unreadable entry across a uid boundary are both
    ordinary races on a live system, not a defect — every caller here treats
    ``None`` as "not measured for this pid/field", never as zero.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        # trw-fail-silent-allow: a vanished/unreadable /proc entry is a normal race on a live system; callers treat None as "not measured", never as zero.
        return None


def _list_numeric_dir(path: Path) -> list[int]:
    """Numeric entry names under *path* (a ``task/`` or pid directory)."""
    try:
        with os.scandir(path) as entries:
            return sorted(int(entry.name) for entry in entries if entry.name.isdigit())
    except OSError:
        # trw-fail-silent-allow: an unreadable/vanished task dir yields zero threads for THIS pid, not a failure of the whole row.
        return []


def _safe_int(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        # trw-fail-silent-allow: a non-numeric /proc field or filename means "cannot parse this one"; caller treats None as absence, not zero.
        return None


def _clk_tck() -> float:
    """``SC_CLK_TCK`` (clock ticks per second), or the POSIX-typical 100 Hz fallback."""
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        ticks = 0
    return float(ticks) if ticks and ticks > 0 else 100.0


def _system_uptime_seconds() -> float | None:
    raw = _read_text(_PROC_ROOT / "uptime")
    if raw is None:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        # trw-fail-silent-allow: a malformed /proc/uptime means uptime is unmeasurable here; caller SKIPs on None, never fabricates a value.
        return None


def _stat_fields(raw: str) -> list[str] | None:
    """Whitespace fields from a ``/proc/.../stat`` line, indexed from field 3 (state).

    Field 2 (``comm``) is parenthesised and may itself contain spaces or
    parentheses, so the split anchors on the LAST ``)`` rather than counting
    whitespace tokens from the start of the line.
    """
    rparen = raw.rfind(")")
    if rparen == -1:
        return None
    rest = raw[rparen + 1 :].split()
    return rest or None


def _process_starttime_ticks(pid: int) -> int | None:
    raw = _read_text(_PROC_ROOT / str(pid) / "stat")
    if raw is None:
        return None
    fields = _stat_fields(raw)
    if fields is None or len(fields) < 20:
        return None
    return _safe_int(fields[19])  # field 22 (starttime); fields[0] == field 3


def _thread_cpu_seconds(pid: int, tid: int, clk_tck: float) -> float | None:
    raw = _read_text(_PROC_ROOT / str(pid) / "task" / str(tid) / "stat")
    if raw is None:
        return None
    fields = _stat_fields(raw)
    if fields is None or len(fields) < 13:
        return None
    utime = _safe_int(fields[11])  # field 14
    stime = _safe_int(fields[12])  # field 15
    if utime is None or stime is None:
        return None
    return (utime + stime) / clk_tck


@dataclass(frozen=True, slots=True)
class _ServerSample:
    pid: int
    uptime_seconds: float
    thread_count: int
    hottest_tid: int | None
    hottest_cpu_seconds: float | None


def _sample_server(pid: int, clk_tck: float, sys_uptime: float) -> _ServerSample | None:
    """Sample *pid*'s uptime and per-thread CPU seconds, or ``None`` if unmeasurable."""
    starttime_ticks = _process_starttime_ticks(pid)
    if starttime_ticks is None:
        return None
    uptime_seconds = max(sys_uptime - (starttime_ticks / clk_tck), 0.001)
    tids = _list_numeric_dir(_PROC_ROOT / str(pid) / "task")
    hottest_tid: int | None = None
    hottest_cpu: float | None = None
    for tid in tids:
        cpu = _thread_cpu_seconds(pid, tid, clk_tck)
        if cpu is None:
            continue
        if hottest_cpu is None or cpu > hottest_cpu:
            hottest_cpu = cpu
            hottest_tid = tid
    return _ServerSample(
        pid=pid,
        uptime_seconds=uptime_seconds,
        thread_count=len(tids),
        hottest_tid=hottest_tid,
        hottest_cpu_seconds=hottest_cpu,
    )


def own_thread_hotspot() -> dict[str, float] | None:
    """This process's own hottest-thread CPU share of its own uptime, or ``None``.

    Read by ``trw_heartbeat`` (PRD-FIX-131 follow-up FR-in-band) so a caller
    sees the same signal in-band without waiting for a ``trw-mcp doctor`` run.
    Samples the CALLING process's own pid. Returns ``None`` on non-Linux or an unreadable ``/proc``
    — the tool-response budget rule omits the key entirely rather than
    shipping a fabricated zero for a platform this cannot measure.
    """
    if not sys.platform.startswith("linux"):
        return None
    sys_uptime = _system_uptime_seconds()
    if sys_uptime is None:
        return None
    sample = _sample_server(os.getpid(), _clk_tck(), sys_uptime)
    if sample is None or sample.hottest_cpu_seconds is None:
        return None
    return {
        "share": round(sample.hottest_cpu_seconds / sample.uptime_seconds, 4),
        "cpu_seconds": round(sample.hottest_cpu_seconds, 1),
    }
