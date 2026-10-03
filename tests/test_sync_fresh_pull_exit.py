"""FRESH-PULL-CLI-EXIT: a short-lived CLI process finishes the pull it started BEFORE the interpreter shuts down.

O6 on 8.1.8.dev15: ``trw-mcp local recall`` on a fresh CLI-only host started the team catch-up on a daemon thread,
waited its 1.5 s budget and exited, which killed the pull mid-flight; the attempt it had recorded then held off every
retry for ``team_sync_fresh_after_seconds``, so the host's recall never saw the team's rows.

O6 on 8.1.8.dev23 showed the first fix (an ``atexit`` join) did not work for a real pull: once the interpreter starts to
shut down, a thread cannot start new asyncio work (``RuntimeError: can't register atexit after shutdown``; DNS goes
through the default executor), so the pull failed 18 ms after the process began to exit. The fake pull below therefore
does real executor work, and the process drains the pull explicitly while it is still fully alive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from pathlib import Path
    from types import SimpleNamespace

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.sync import _fresh_pull
    from trw_mcp.sync.coordinator import SyncCoordinator

    trw_dir = Path(sys.argv[1])
    cycle_seconds = float(sys.argv[2])
    _fresh_pull.EXIT_GRACE_SECONDS = float(sys.argv[3])

    class Slow:
        _trw_dir = trw_dir
        _targets = [SimpleNamespace(label="primary")]
        _coordinator = SyncCoordinator(trw_dir)
        _pages = 1

        async def _run_one_cycle(self, force=False, *, push=True, pull=True):
            await asyncio.sleep(cycle_seconds)
            # the network step of a real pull: name resolution goes through the loop's default executor
            await asyncio.get_running_loop().getaddrinfo("localhost", 80)
            seq = self._coordinator.get_last_pull_seq()
            if Slow._pages > 0:
                Slow._pages -= 1
                seq += 200
            self._coordinator.record_pull_success(pull_seq=seq)
            return "ok"

    _fresh_pull._client = lambda _config, _dir: Slow()
    answer = _fresh_pull.ensure_fresh(trw_dir, TRWConfig(team_sync_enabled=True), budget_seconds=0.5)
    landed = _fresh_pull.finish_inflight()  # what the CLI does before it returns, while the interpreter is alive
    print(answer["status"], landed)
    """
)


def _exit_run(
    trw_dir: Path, cycle_seconds: float, grace_seconds: float
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", _CHILD, str(trw_dir), str(cycle_seconds), str(grace_seconds)],
        env={**os.environ, "TRW_PLATFORM_CONTACT_ENABLED": "true"},  # the suite defaults it off; this child pulls
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return done, time.monotonic() - started


def test_a_process_drains_the_pull_it_started_before_it_exits(tmp_path: Path) -> None:
    from trw_mcp.sync.coordinator import SyncCoordinator

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    done, _ = _exit_run(trw_dir, 1.2, 5.0)

    assert done.stdout.strip() == "timeout True", done.stderr[-600:]
    assert SyncCoordinator(trw_dir).get_last_pull_seq() == 200


def test_a_pull_that_outlasts_the_grace_cannot_hold_the_process(tmp_path: Path) -> None:
    from trw_mcp.sync.coordinator import SyncCoordinator

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    done, elapsed = _exit_run(trw_dir, 30.0, 0.5)

    assert done.stdout.strip() == "timeout False", done.stderr[-600:]
    assert elapsed < 10.0  # the 30 s cycle was abandoned after the 0.5 s grace, not awaited
    assert SyncCoordinator(trw_dir).get_last_pull_seq() == 0


def test_nothing_in_flight_means_nothing_to_wait_for() -> None:
    from trw_mcp.sync import _fresh_pull

    _fresh_pull.reset_inflight()

    assert _fresh_pull.finish_inflight() is False
