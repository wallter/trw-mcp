"""HOTSWAP-AUTO: a shared server replaces itself when the installed trw-mcp / trw-memory changes.

Every decision runs against injected ports (no subprocess, no sockets); the real ports have their own tests below.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from trw_memory.daemon._discovery import DaemonInfo

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _autoswap
from trw_mcp.shared_server._autoswap import HotSwap, Ports
from trw_mcp.shared_server._records import SharedServerError

pytestmark = pytest.mark.unit

OLD = {"trw-mcp": "8.1.0", "trw-memory": "5.1.0"}
NEW = {"trw-mcp": "8.1.1", "trw-memory": "5.1.1"}
ME = 4242


def _daemon(version: str, *, capabilities: tuple[str, ...] = ("drain",)) -> DaemonInfo:
    return DaemonInfo(
        pid=777,
        url="http://127.0.0.1:9/mcp",
        started_at="2026-09-30T00:00:00+00:00",
        version=version,
        capabilities=list(capabilities),
    )


class Rig:
    """One HotSwap over fakes; every attribute is what the next poll will see."""

    def __init__(self) -> None:
        self.hint = dict(OLD)
        self.record: tuple[str, str | None] = ("/venv/bin/python", None)
        self.probe_result: dict[str, str] | None = dict(NEW)
        self.live: int | None = ME
        self.claim_busy = False
        self.claim_during_probe = False
        self.lock_free = True
        self.spawn_error: str | None = None
        self.daemon: DaemonInfo | None = None
        self.drain_result = ""
        self.now = 0.0
        self.probed: list[tuple[str, str | None]] = []
        self.spawned = 0
        self.drained: list[str] = []
        self.last: list[dict[str, Any]] = []
        self.door = SimpleNamespace(draining=False)
        self.swap = HotSwap(
            "stable",
            self.door,
            Ports(
                installed=lambda: dict(self.hint),
                recorded=lambda: self.record,
                probe=self._probe,
                live_pid=lambda: self.live,
                claim_busy=lambda: self.claim_busy,
                spawn_lock=self._lock,
                start_successor=self._spawn,
                daemon=lambda: self.daemon,
                drain_daemon=self._drain,
                write_last=self.last.append,
            ),
            booted=dict(OLD),
            hint=dict(OLD),
            record=self.record,
            pid=ME,
            clock=lambda: self.now,
        )

    def _probe(self, python: str, pythonpath: str | None) -> dict[str, str] | None:
        self.probed.append((python, pythonpath))
        self.claim_busy = self.claim_busy or self.claim_during_probe  # a manual swap's successor starts mid-probe
        return self.probe_result

    @contextlib.contextmanager
    def _lock(self) -> Iterator[bool]:
        yield self.lock_free

    def _spawn(self) -> Any:
        self.spawned += 1
        if self.spawn_error:
            raise SharedServerError(self.spawn_error)
        return SimpleNamespace(pid=9, version=NEW["trw-mcp"])

    def _drain(self, info: DaemonInfo, mine: str) -> str:
        self.drained.append(mine)
        return self.drain_result

    async def polls(self, n: int) -> None:
        for _ in range(n):
            await self.swap.step()


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --------------------------------------------------------------------------- the server swap


async def test_nothing_changed_means_no_probe_and_no_spawn(rig: Rig) -> None:
    await rig.polls(5)
    assert (rig.probed, rig.spawned, rig.last) == ([], 0, [])


async def test_a_metadata_change_swaps_only_on_the_second_agreeing_poll(rig: Rig) -> None:
    rig.hint = dict(NEW)  # `pip install` just replaced the dist-info
    await rig.polls(1)
    assert (rig.probed, rig.spawned) == ([], 0), "a first sighting never swaps: an install is not atomic"
    await rig.polls(1)
    assert rig.probed == [("/venv/bin/python", None)] and rig.spawned == 1


async def test_a_hint_that_keeps_moving_never_swaps(rig: Rig) -> None:
    for version in ("8.1.1", "8.1.2", "8.1.3", "8.1.4"):
        rig.hint = {**NEW, "trw-mcp": version}
        await rig.polls(1)
    assert (rig.probed, rig.spawned) == ([], 0)


async def test_a_broken_install_never_swaps_and_is_retried_next_poll(rig: Rig) -> None:
    rig.hint, rig.probe_result = dict(NEW), None  # dist-info new, but the import fails: half-finished pip
    await rig.polls(3)
    assert rig.spawned == 0 and len(rig.probed) == 2
    rig.probe_result = dict(NEW)  # pip finished
    await rig.polls(1)
    assert rig.spawned == 1


async def test_a_same_version_reinstall_is_adopted_not_swapped(rig: Rig) -> None:
    rig.hint, rig.probe_result = {**OLD, "trw-mcp": "8.1.0+rebuilt"}, dict(OLD)
    await rig.polls(6)
    assert rig.spawned == 0 and len(rig.probed) == 1, "probed once, then the new hint is the baseline"


async def test_the_env_record_pointing_elsewhere_swaps_without_a_metadata_change(rig: Rig) -> None:
    rig.record = ("/envs/canary/venv-8.1.1/bin/python", None)  # canary-style: a different isolated venv
    await rig.polls(1)
    assert rig.spawned == 0
    await rig.polls(1)
    assert rig.probed == [("/envs/canary/venv-8.1.1/bin/python", None)] and rig.spawned == 1


async def test_a_record_that_flips_between_polls_does_not_count_as_agreement(rig: Rig) -> None:
    rig.record = ("/envs/a/bin/python", None)
    await rig.polls(1)
    rig.record = ("/envs/b/bin/python", None)
    await rig.polls(1)
    assert (rig.probed, rig.spawned) == ([], 0)


async def test_a_src_worktree_env_is_left_to_the_operator(rig: Rig) -> None:
    rig.record = ("/venv/bin/python", "/wt/trw-mcp/src")
    rig.swap = HotSwap(
        "stable",
        rig.door,
        rig.swap._ports,
        booted=dict(OLD),
        hint=dict(OLD),
        record=rig.record,
        pid=ME,
        clock=lambda: rig.now,
    )
    rig.hint = dict(NEW)
    await rig.polls(4)
    assert (rig.probed, rig.spawned) == ([], 0)
    assert "swap --src" in rig.swap.status()["detail"]


# --------------------------------------------------------------------------- single flight


async def test_a_draining_or_superseded_server_never_starts_a_successor(rig: Rig) -> None:
    rig.hint = dict(NEW)
    rig.door.draining = True
    await rig.polls(3)
    assert (rig.probed, rig.spawned) == ([], 0)
    rig.door.draining = False
    rig.live = ME + 1  # the record already names another server
    await rig.polls(3)
    assert (rig.probed, rig.spawned) == ([], 0)
    rig.live = None  # record gone: this server is no longer the env's server either
    await rig.polls(3)
    assert rig.spawned == 0


async def test_a_successor_already_booting_blocks_a_second_one(rig: Rig) -> None:
    rig.hint, rig.claim_busy = dict(NEW), True  # <env>.lock is held from a successor's start until its flip
    await rig.polls(4)
    assert (rig.probed, rig.spawned) == ([], 0)
    rig.claim_busy = False
    await rig.polls(1)
    assert rig.spawned == 1


async def test_a_successor_that_begins_booting_during_the_probe_blocks_the_spawn(rig: Rig) -> None:
    rig.hint, rig.claim_during_probe = dict(NEW), True
    await rig.polls(3)
    assert rig.probed and rig.spawned == 0


async def test_the_spawn_lock_held_elsewhere_blocks_the_spawn(rig: Rig) -> None:
    rig.hint, rig.lock_free = dict(NEW), False
    await rig.polls(3)
    assert rig.spawned == 0
    rig.lock_free = True
    await rig.polls(1)
    assert rig.spawned == 1


async def test_the_record_is_rechecked_under_the_lock(rig: Rig) -> None:
    rig.hint = dict(NEW)
    reads = iter([ME, ME + 1])  # ours at the pre-check, taken over by the time the lock is held
    rig.swap._ports = Ports(**{**rig.swap._ports.__dict__, "live_pid": lambda: next(reads)})
    await rig.polls(2)
    assert rig.spawned == 0


# --------------------------------------------------------------------------- failure and observability


async def test_a_failed_successor_leaves_the_server_serving_and_backs_off(rig: Rig) -> None:
    rig.hint, rig.spawn_error = dict(NEW), "the shared trw-mcp for 'stable' exited (1) before serving"
    await rig.polls(2)
    assert rig.spawned == 1 and not rig.swap.finished
    assert rig.last[-1]["outcome"].startswith("failed") and "exited (1)" in rig.last[-1]["outcome"]
    await rig.polls(5)
    assert rig.spawned == 1, "inside the backoff nothing is retried"
    rig.now += 301
    await rig.polls(1)
    assert rig.spawned == 2
    rig.now += 301
    await rig.polls(1)
    assert rig.spawned == 2, "the second backoff is longer than the first"
    rig.now += 300
    await rig.polls(1)
    assert rig.spawned == 3
    rig.now += 10**6
    await rig.polls(4)
    assert rig.spawned == 3, "the third failure ends the retries of that target"
    assert "gave up" in rig.swap.status()["detail"]


async def test_a_newer_target_after_a_failure_is_tried_at_once(rig: Rig) -> None:
    rig.hint, rig.spawn_error = dict(NEW), "boom"
    await rig.polls(2)
    rig.spawn_error = None
    rig.hint = {**NEW, "trw-mcp": "8.1.2"}
    await rig.polls(2)
    assert rig.spawned == 2 and rig.swap.finished


async def test_a_swap_is_recorded_for_the_successor_to_show_and_the_watcher_stops(rig: Rig) -> None:
    rig.hint = dict(NEW)
    await rig.polls(2)
    assert [row["outcome"] for row in rig.last] == ["swapping", "swapped"]
    last = rig.last[-1]
    assert last["from"] == "8.1.0" and last["to"] == "8.1.1" and last["from_pid"] == ME and last["to_pid"] == 9
    assert last["at"].endswith("+00:00")
    assert rig.swap.finished
    await rig.polls(3)
    assert rig.spawned == 1


async def test_each_decision_is_one_structured_log_line_and_repeats_stay_quiet(rig: Rig) -> None:
    rig.hint = dict(NEW)
    with structlog.testing.capture_logs() as logs:
        await rig.polls(2)
    decisions = [(e["decision"]) for e in logs if e["event"] == "shared_mcp_autoswap"]
    assert decisions == ["drift_seen", "probe_ok", "successor_published"]
    drift = next(e for e in logs if e.get("decision") == "drift_seen")
    assert drift["env"] == "stable" and drift["to"] == "8.1.1"

    quiet = Rig()
    quiet.hint, quiet.probe_result = dict(NEW), None
    with structlog.testing.capture_logs() as logs:
        await quiet.polls(6)
    failed = [e for e in logs if e.get("decision") == "probe_failed"]
    assert len(failed) == 1, "a broken install logs once, not every poll"


# --------------------------------------------------------------------------- the memory daemon


async def test_an_older_daemon_is_drained_by_handshake_once_the_server_is_current(rig: Rig) -> None:
    rig.daemon = _daemon("5.0.9")
    await rig.polls(1)
    assert rig.drained == [OLD["trw-memory"]]
    assert rig.swap.status()["daemon"].startswith("drained")


@pytest.mark.parametrize("version", ["5.1.0", "5.2.0", "6.0.0", "unknown"], ids=["equal", "newer", "major", "opaque"])
async def test_an_equal_or_newer_or_unreadable_daemon_is_never_touched(rig: Rig, version: str) -> None:
    rig.daemon = _daemon(version)
    await rig.polls(3)
    assert rig.drained == []


async def test_a_daemon_without_the_handshake_is_reported_not_signalled(rig: Rig) -> None:
    rig.daemon = _daemon("5.0.0", capabilities=())
    with structlog.testing.capture_logs() as logs:
        await rig.polls(4)
    assert rig.drained == []
    assert [e["decision"] for e in logs if e["event"] == "shared_mcp_autoswap"] == ["daemon_unsupported"]


async def test_a_busy_daemon_keeps_serving_and_the_drain_is_retried_with_backoff(rig: Rig) -> None:
    rig.daemon, rig.drain_result = _daemon("5.0.9"), "it did not drain (busy: 2 call(s) from other sessions)"
    await rig.polls(3)
    assert len(rig.drained) == 1
    rig.now += 121
    await rig.polls(1)
    assert len(rig.drained) == 2
    rig.now += 121
    await rig.polls(1)
    assert len(rig.drained) == 2, "each failed attempt doubles the wait"
    rig.drain_result = ""
    rig.now += 121
    await rig.polls(1)
    assert len(rig.drained) == 3


async def test_a_refused_drain_is_logged_as_refused_not_as_busy(rig: Rig) -> None:
    rig.daemon, rig.drain_result = _daemon("5.0.9"), "no usable memory grant for the drain: none"
    with structlog.testing.capture_logs() as logs:
        await rig.polls(1)
    row = next(e for e in logs if e["event"] == "shared_mcp_autoswap")
    assert row["decision"] == "daemon_refused" and "no usable memory grant" in row["why"]


async def test_the_drain_gives_up_after_five_attempts_and_says_to_drain_manually(rig: Rig) -> None:
    rig.daemon, rig.drain_result = _daemon("5.0.9"), "it did not drain (busy: 2 call(s) from other sessions)"
    with structlog.testing.capture_logs() as logs:
        for _ in range(9):
            rig.now += 4000  # past every backoff: only the attempt budget can stop it
            await rig.polls(1)
    assert len(rig.drained) == 5
    assert [e["decision"] for e in logs if e["event"] == "shared_mcp_autoswap"].count("daemon_gave_up") == 1
    assert "drain it manually" in rig.swap.status()["daemon"] and "swap --daemon" in rig.swap.status()["daemon"]


async def test_a_respawned_older_daemon_spends_the_same_budget_not_a_fresh_one(rig: Rig) -> None:
    """An old client that re-spawns an older daemon after every drain must not make the drain loop forever."""
    rig.drain_result = ""
    for n in range(8):
        rig.daemon = _daemon("5.0.9")
        rig.daemon = rig.daemon.model_copy(update={"pid": 800 + n, "started_at": f"t{n}"})  # a new instance each time
        rig.now += 4000
        await rig.polls(1)
    assert len(rig.drained) == 5


async def test_a_quiet_hour_starts_a_fresh_drain_episode(rig: Rig) -> None:
    rig.daemon, rig.drain_result = _daemon("5.0.9"), "it did not drain (busy: 1 call(s) from other sessions)"
    for _ in range(6):
        rig.now += 4000
        await rig.polls(1)
    assert len(rig.drained) == 5
    rig.daemon = None
    rig.now += 4000
    await rig.polls(1)  # nothing older for an hour: the episode is over
    rig.daemon, rig.drain_result = _daemon("5.0.8"), ""
    rig.now += 10
    await rig.polls(1)
    assert len(rig.drained) == 6


async def test_the_daemon_waits_for_a_pending_server_swap(rig: Rig) -> None:
    rig.daemon, rig.hint = _daemon("5.0.9"), dict(NEW)
    await rig.polls(1)  # drift seen, swap pending: the newest code drains the daemon, not this server
    assert rig.drained == []


# --------------------------------------------------------------------------- surfaces


async def test_the_door_status_carries_the_watcher_state() -> None:
    from trw_mcp.shared_server._server import Door

    class _App:
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None: ...

    door = Door(_App(), token="t", env="stable", version="1", max_inflight=1)
    door.extra_status = lambda: {"auto_swap": {"enabled": True, "state": "watching"}}
    assert door.status()["auto_swap"] == {"enabled": True, "state": "watching"}


def test_auto_swap_is_on_by_default_and_polls_every_fifteen_seconds() -> None:
    config = SharedMcpConfig()
    assert config.auto_swap is True and config.auto_swap_poll_seconds == 15
    assert SharedMcpConfig(auto_swap=False).auto_swap is False
    with pytest.raises(ValueError):
        SharedMcpConfig(auto_swap_poll_seconds=0)


def test_the_drift_advisory_says_a_shared_server_swaps_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.middleware import version_drift

    default = version_drift.build_advisory("1", "2")["action"]
    assert "restart" in default
    monkeypatch.setattr(version_drift, "_ACTION_OVERRIDE", "this shared trw-mcp hot-swaps on its own")
    assert version_drift.build_advisory("1", "2")["action"] == "this shared trw-mcp hot-swaps on its own"


@pytest.mark.parametrize(
    ("daemon_version", "mine", "older"),
    [
        ("8.1.2.dev5", "8.1.2", True),
        ("5.1.0.dev10", "5.1.0.dev11", True),
        ("5.1.0.dev11", "5.1.0.dev10", False),
        ("5.1.0.dev10", "5.1.0", True),
        ("5.1.0", "5.1.0.dev10", False),
        ("5.1.1", "5.1.1", False),
        ("5.0.9", "5.1.0", True),
        ("6.0.0", "5.9.9", False),
        ("unknown", "5.1.1", False),
    ],
)
def test_strictly_older_orders_prereleases_and_never_guesses(daemon_version: str, mine: str, older: bool) -> None:
    assert _autoswap.strictly_older(daemon_version, mine) is older


async def test_a_dev_build_upgrade_drains_the_previous_dev_daemon(rig: Rig) -> None:
    rig.swap._booted = {"trw-mcp": "8.1.1.dev11", "trw-memory": "5.1.1.dev11"}
    rig.daemon = _daemon("5.1.1.dev10")
    await rig.polls(1)
    assert rig.drained == ["5.1.1.dev11"]


@pytest.mark.parametrize(
    ("hint", "kept"),
    [
        ({"trw-mcp": "8.1.0", "trw-memory": "5.1.0", "trw-distill": "0.9"}, True),  # the install matches what booted
        ({"trw-mcp": "8.1.1", "trw-memory": "5.1.0"}, False),  # upgraded after the code was imported
        ({"trw-mcp": "8.1.0"}, False),  # mid-install: a package unreadable
    ],
)
def test_the_baseline_hint_never_absorbs_an_install_the_process_did_not_boot(hint: dict[str, str], kept: bool) -> None:
    assert _autoswap.baseline_hint(OLD, hint) == (hint if kept else {})


async def test_an_upgrade_between_import_and_the_first_poll_is_still_swapped(rig: Rig) -> None:
    rig.swap = HotSwap(
        "stable",
        rig.door,
        rig.swap._ports,
        booted=dict(OLD),
        hint=_autoswap.baseline_hint(OLD, dict(NEW)),  # the metadata already said NEW when the watcher started
        record=rig.record,
        pid=ME,
        clock=lambda: rig.now,
    )
    rig.hint = dict(NEW)
    await rig.polls(2)
    assert rig.spawned == 1
