"""``trw-mcp doctor`` row for hot worker threads on live trw-mcp servers.

Kept out of ``_subcommands_doctor.py`` (already close to the module-size gate)
as a sibling, the same shape ``_doctor_memory_daemon``/``_doctor_memory_wal``
use.

Incident this exists to surface (2026-09-05): three live trw-mcp stdio servers
each had ONE worker thread burning 70-85% of process CPU for hours (one
measured at 6,864s of user CPU after 2.5h uptime) while ``trw_deliver`` hung
for 1,800s and ``trw_session_start`` took 111-353s. ``py-spy``/``gdb`` need
ptrace (``ptrace_scope=1``, no sudo on the affected boxes), so the only
zero-permission diagnostic is ``/proc`` itself — the same source
``kill -USR1 <pid>`` reads via ``faulthandler`` (``server/_cli.py``). This row
is the difference between "I have to already suspect a pid" and "doctor tells
me which pid to signal".

The check is a **read-only /proc census**, never a signal: reporting a hot
thread must not be the thing that fixes it, and this module never sends
SIGUSR1 itself — the WARN message names the remedy for an operator to run.

Four outcomes, and the status mapping is the load-bearing part:

``non-Linux, or /proc unreadable``
    SKIP, "NOT MEASURED" — never PASS. A platform or environment this cannot
    inspect must not report a clean bill of health it never actually checked.
``no live trw-mcp server processes discovered``
    PASS. The writer-lock registry naming zero live servers is a genuine,
    positive measurement (nothing to warn about), not an absence of one.
``every discovered server's hottest thread is below BOTH thresholds``
    PASS, with the hottest observed share reported for context.
``a server's hottest thread clears BOTH doctor_thread_hotspot_share (relative
to /proc-measured process uptime) AND doctor_thread_hotspot_min_seconds
(absolute floor)``
    WARN, naming the pid, the hot thread id, its CPU seconds and uptime share,
    and the exact remedy: ``kill -USR1 <pid>`` plus where the stack dump lands.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.state.memory_pressure import _pid_is_alive

logger = structlog.get_logger(__name__)

__all__ = ["own_thread_hotspot", "thread_hotspot_row"]

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


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        # trw-fail-silent-allow: same /proc race as _read_text above.
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


def _cmdline_names_trw_mcp(pid: int) -> bool:
    raw = _read_bytes(_PROC_ROOT / str(pid) / "cmdline")
    return raw is not None and b"trw-mcp" in raw


def _discover_server_pids(target: Path) -> list[int]:
    """Live trw-mcp server pids named by ``memory.db.writers/*.lock``.

    A lock names a session that recently wrote to the memory store, not
    necessarily a still-live server, so both liveness (``os.kill(pid, 0)``)
    and identity (``cmdline`` names ``trw-mcp``) are verified before a pid is
    trusted — a recycled pid or an unrelated process must never be reported
    as a hot trw-mcp server.
    """
    writers_dir = target / ".trw" / "memory" / "memory.db.writers"
    try:
        with os.scandir(writers_dir) as entries:
            names = [entry.name for entry in entries if entry.name.endswith(".lock")]
    except OSError:
        # trw-fail-silent-allow: no writer registry means zero discoverable servers — this row's own "no live server" PASS, not a scan failure.
        return []
    pids: set[int] = set()
    for name in names:
        pid = _safe_int(name[: -len(".lock")])
        if pid is not None and _pid_is_alive(pid) and _cmdline_names_trw_mcp(pid):
            pids.add(pid)
    return sorted(pids)


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


def _server_line(
    sample: _ServerSample,
    dump_dir: Path,
    *,
    share_threshold: float,
    min_seconds: float,
) -> tuple[str, str]:
    """One server's report line and its own verdict (``PASS``/``WARN``)."""
    if sample.hottest_cpu_seconds is None or sample.hottest_tid is None:
        return "PASS", f"pid {sample.pid}: NOT MEASURED (no readable thread stat)."
    share = sample.hottest_cpu_seconds / sample.uptime_seconds
    base = (
        f"pid {sample.pid} (up {sample.uptime_seconds:.0f}s, {sample.thread_count} threads): "
        f"hottest tid {sample.hottest_tid} burned {sample.hottest_cpu_seconds:.0f}s CPU "
        f"({share:.0%} of uptime)"
    )
    if sample.hottest_cpu_seconds >= min_seconds and share >= share_threshold:
        dump_path = dump_dir / f"thread-dump-{sample.pid}.txt"
        return "WARN", (
            f"{base}, threshold {share_threshold:.0%}/{min_seconds:.0f}s exceeded -- "
            f"run 'kill -USR1 {sample.pid}' for a full stack dump at {dump_path}."
        )
    return "PASS", f"{base}, below threshold {share_threshold:.0%}/{min_seconds:.0f}s."


def thread_hotspot_row(
    target: Path,
    *,
    share_threshold: float,
    min_seconds: float,
    is_linux: bool,
) -> tuple[str, str]:
    """Return ``(status, message)`` describing the hottest thread per live trw-mcp server.

    Args:
        target: project root whose ``.trw/memory/memory.db.writers`` names the
            candidate server pids.
        share_threshold: ``doctor_thread_hotspot_share`` — fraction of process
            uptime the hottest thread's CPU seconds must clear to WARN.
        min_seconds: ``doctor_thread_hotspot_min_seconds`` — absolute CPU-second
            floor the hottest thread must also clear.
        is_linux: the caller's platform check (``sys.platform.startswith
            ("linux")``), passed in rather than read here so tests can force
            either branch without patching ``sys.platform`` process-wide.
    """
    if not is_linux:
        return "SKIP", "NOT MEASURED: thread-hotspot census reads /proc, which only exists on Linux."

    sys_uptime = _system_uptime_seconds()
    if sys_uptime is None:
        return "SKIP", f"NOT MEASURED: {_PROC_ROOT / 'uptime'} is unreadable."

    pids = _discover_server_pids(target)
    if not pids:
        return "PASS", "no live trw-mcp server processes detected via memory.db.writers locks."

    clk_tck = _clk_tck()
    dump_dir = target / ".trw" / "logs"
    overall = "PASS"
    lines: list[str] = []
    for pid in pids:
        sample = _sample_server(pid, clk_tck, sys_uptime)
        if sample is None:
            lines.append(f"pid {pid}: NOT MEASURED (/proc/{pid}/stat unreadable).")
            continue
        status, line = _server_line(sample, dump_dir, share_threshold=share_threshold, min_seconds=min_seconds)
        if status == "WARN":
            overall = "WARN"
            logger.warning("doctor_thread_hotspot_warn", pid=pid, tid=sample.hottest_tid)
        lines.append(line)
    return overall, " | ".join(lines)


def own_thread_hotspot() -> dict[str, float] | None:
    """This process's own hottest-thread CPU share of its own uptime, or ``None``.

    Read by ``trw_heartbeat`` (PRD-FIX-131 follow-up FR-in-band) so a caller
    sees the same signal in-band without waiting for a ``trw-mcp doctor`` run.
    Reuses the doctor row's per-server sample, called with the CALLING
    process's own pid. Returns ``None`` on non-Linux or an unreadable ``/proc``
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
