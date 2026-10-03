"""Name the test that escaped its isolated HOME and started a memory daemon in the worker's session home.

DAEMON-LEAK-INTERMITTENT: a mid-test ``monkeypatch.undo()`` also reverts the autouse HOME / ``TRW_USER_DIR``
redirect (it is the same ``monkeypatch``), so a later store call auto-starts a daemon under the SESSION home
(``session_trw_home``), which the per-test reap never looked at. The session-end sweep then failed the whole run
with every test passing and no test named (``test_ki1_r7_guarded_copy.py::test_c4_ledger_and_later_update``).
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

#: Where ``_trw_home._redirect_home`` puts a home's daemon discovery file (``TRW_USER_DIR`` = ``<home>/.trw-user``).
_DISCOVERY = Path(".trw-user") / "memory" / "daemon.json"


def session_homes(basetemp: Path) -> list[Path]:
    """The worker's session home(s): ``session_trw_home`` makes them with ``mktemp("trw-session-home")``."""
    return sorted(basetemp.glob("trw-session-home*"))


Published = dict[int, tuple[Path, "str | None"]]


def published_daemon_pids(homes: list[Path]) -> Published:
    """Every daemon pid published under *homes*, with its recorded ``process_start``, by discovery file.

    Read with ``os.open``/``os.read``, never ``Path.read_text``/``open``: this runs in every test's setup and
    teardown, and some tests patch those to fail. A file that cannot be read or parsed publishes nothing.
    """
    found: Published = {}
    for home in homes:
        path = home / _DISCOVERY
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                record = json.loads(os.read(fd, 1 << 16).decode("utf-8"))
            finally:
                os.close(fd)
            pid, start = record.get("pid"), record.get("process_start")
        except (
            OSError,
            ValueError,
            AttributeError,
        ):  # trw-fail-silent-allow: absent or half-written: nothing published
            continue
        if isinstance(pid, int) and pid > 0:
            found[pid] = (path, start if isinstance(start, str) else None)
    return found


def stop_escaped(before: Published, after: Published) -> list[str]:
    """SIGTERM each daemon published during the test (in *after*, not *before*); describe each for the failure.

    A pid whose start no longer matches the one its discovery file recorded was reused by another process: it is
    left alone (codex r1 on L1-DAEMON-LEAK), since signalling it could stop something that is not a daemon.
    """
    from trw_memory.storage._pid_liveness import process_start

    escaped = []
    for pid, (path, recorded) in sorted(after.items()):
        if pid in before:
            continue
        now = process_start(pid)
        if recorded is not None and now is not None and now != recorded:
            continue
        _stop(pid)
        escaped.append(f"pid {pid} ({path})")
    return escaped


def _stop(pid: int, grace_s: float = 10.0) -> None:
    """SIGTERM *pid* and wait for it (SIGKILL after *grace_s*), so the session-end sweep does not count it again."""
    try:
        os.kill(pid, signal.SIGTERM)  # the daemon removes its discovery file and lock on SIGTERM
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            try:  # a daemon this process spawned directly is its child: reap the zombie, or it looks alive
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:  # trw-fail-silent-allow: a detached daemon is not our child
                pass
            os.kill(pid, 0)
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:  # trw-fail-silent-allow: it is gone, which is the goal
        return


def escape_message(escaped: list[str]) -> str:
    return (
        "this test started a memory daemon OUTSIDE its isolated HOME, in the worker's session home: "
        + ", ".join(escaped)
        + ". A mid-test monkeypatch.undo() also reverts the autouse HOME/TRW_USER_DIR redirect; undo only what the "
        "test patched (`with monkeypatch.context() as mp:`), or use no_memory_daemon (DAEMON-LEAK-INTERMITTENT)."
    )
