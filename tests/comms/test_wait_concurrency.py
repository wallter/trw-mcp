"""FR11 process guard, real stdio cancellation and concurrent SQLite work.

The helper uses a pipe barrier or observes unchanged default time.sleep and
observes SDK cancel scopes after their original cancel implementation runs. No timer guesses,
process-global identity switching, fake cancellation checks or skipped defects.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms._wait_transport_support import WaitPeer
from tests.comms.conftest import joined_member
from tests.comms.test_policy import SendScene, scene  # noqa: F401


@pytest.fixture
def pair(formation_env: FormationFixture, tmp_path: Path) -> Any:
    from trw_mcp.comms._store import database_path

    for member, session in (("impl-1", "pin-a"), ("impl-2", "pin-b")):
        joined_member(formation_env, member, session)
    children: list[WaitPeer] = []
    try:
        for session in ("pin-a", "pin-b"):
            peer = WaitPeer(formation_env.project_root, tmp_path / "peers", session)
            children.append(peer)
            assert peer.call("trw_inbox", action="enroll")["status"] == "ok"
        yield (*children, database_path(formation_env.manifest_path()))
    finally:
        for peer in children:
            peer.close()


def rows(path: Path, sql: str) -> list[Any]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def stable_state(path: Path) -> dict[str, list[Any]]:
    """Snapshot mailbox state except the ordinary-fetch group's monotonic clock.

    A fetch may advance group_time; policy, charge, closure and identity fields
    must not change merely because a wait was cancelled.
    """
    result = {
        name: rows(path, f"SELECT * FROM {name}")
        for name in ("endpoints", "admissions", "milestones", "refusal_counts")
    }
    columns = [row[1] for row in rows(path, "PRAGMA table_info(groups)") if row[1] != "group_time"]
    result["groups"] = rows(path, "SELECT " + ",".join(columns) + " FROM groups")
    return result


def test_spawn_failure_closes_every_barrier_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors: list[int] = []
    original_pipe = os.pipe

    def pipe() -> tuple[int, int]:
        ends = original_pipe()
        descriptors.extend(ends)
        return ends

    def fail_spawn(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected Popen failure")

    monkeypatch.setattr(os, "pipe", pipe)
    monkeypatch.setattr(subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="injected Popen failure"):
        WaitPeer(tmp_path, tmp_path / "scratch", "failed")
    assert len(descriptors) == 4
    for descriptor in descriptors:
        with pytest.raises(OSError) as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF


def begin_wait(peer: WaitPeer, *, native_sleep: bool = False) -> int:
    assert peer.call("probe", arm=True, native_sleep=native_sleep) == {"worker": False, "guard": False}
    request = peer.start("trw_inbox", wait_seconds=30)
    expected: dict[str, Any] = {"event": "sleep", "connections": 0, "guard": True}
    if native_sleep:
        expected["native_sleep"] = True
    assert peer.event("sleep") == expected
    assert peer.call("probe") == {"worker": True, "guard": True}
    return request


def assert_cancelled_exit(peer: WaitPeer) -> None:
    assert peer.event("exit") == {"event": "exit", "guard": False, "connections": 0, "error": "CancelledError"}


def assert_no_normal_response(peer: WaitPeer, request: int) -> None:
    assert not [frame for frame in peer.frames if frame.get("id") == request and "result" in frame]


def test_atomic_guard_admits_one_of_sixteen_simultaneous_callers(scene: SendScene) -> None:
    import trw_mcp.comms as comms
    from tests.comms.conftest import call_peers

    scene.config.comms_wait_max_seconds = 30
    assert call_peers(scene.server, "enroll")["status"] == "ok"
    barrier = threading.Barrier(16)
    release, all_refused = threading.Event(), threading.Event()
    refused: list[dict[str, Any]] = []
    holders: list[int] = []
    lock = threading.Lock()

    def hold(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        holders.append(threading.get_ident())
        assert release.wait(15)
        payload = kwargs["last_empty"]
        assert isinstance(payload, dict)
        return payload

    def invoke() -> dict[str, Any]:
        barrier.wait(timeout=10)
        result = comms.inbox(wait_seconds=30)
        with lock:
            if result.get("reason") == "wait_already_active":
                refused.append(result)
                if len(refused) == 15:
                    all_refused.set()
        return result

    scene.monkeypatch.setattr(comms, "run_bounded_wait", hold)
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(invoke) for _ in range(16)]
        try:
            assert all_refused.wait(12), "fewer than 15 competing callers refused"
            assert len(holders) == 1
            assert comms._WAIT_GUARD.locked()
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]
    assert sum(result.get("items") == [] for result in results) == 1
    assert len(refused) == 15
    assert not comms._WAIT_GUARD.locked()
    assert comms.inbox(wait_seconds=30)["items"] == []


def test_sleep_releases_database_and_other_process_can_send_and_ack(pair: Any) -> None:
    waiting, other, path = pair
    seed = waiting.call("trw_send", recipient_member_id="impl-2", request_key="seed", body="ack me")
    message = seed["receipt"]["message_id"]
    request = begin_wait(waiting)
    # Independent connection gets a real write lock while the wait is sleeping.
    with closing(sqlite3.connect(path, timeout=0)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    # The WAITER's endpoint must not change while it waits (FR11). The other member's own
    # ACK and send renew ITS lease by design (PRD-CORE-274 FR12), so it is not compared.
    before_endpoints = rows(path, "SELECT * FROM endpoints WHERE member_id='impl-1'")
    ack = other.call("trw_inbox", action="ack", message_ids=[message])
    assert ack["acknowledged_ids"] == [message]
    assert other.call("trw_inbox", action="ack", message_ids=[message]) == ack
    args = {"recipient_member_id": "impl-1", "request_key": "during-wait", "body": "reply"}
    sent = other.call("trw_send", **args)
    assert other.call("trw_send", **args) == sent
    waiting.release()
    assert waiting.reply(request)["items"] == [{**sent["receipt"], "body": "reply"}]
    assert waiting.event("exit")["error"] is None
    assert rows(path, "SELECT * FROM endpoints WHERE member_id='impl-1'") == before_endpoints
    assert rows(path, "SELECT charge FROM groups") == [(2,)]
    assert rows(path, "SELECT COUNT(*) FROM milestones WHERE fact='acked'") == [(1,)]
    assert waiting.call("probe") == {"worker": False, "guard": False}


@pytest.mark.parametrize("phase", ["sleep", "sqlite"])
def test_real_cancel_notification_releases_worker_without_undoing_committed_attempt(pair: Any, phase: str) -> None:
    waiting, other, path = pair
    before = stable_state(path)
    clock_before = rows(path, "SELECT group_time FROM groups")[0][0]
    request = begin_wait(waiting)
    # PRD-CORE-274 FR12: the wait's first attempt is an ordinary fetch and renews the
    # waiter's lease; FR11 forbids any further endpoint change by retries or cancellation.
    before["endpoints"] = stable_state(path)["endpoints"]
    conn = sqlite3.connect(path, isolation_level=None)
    expected_groups = before["groups"]
    try:
        if phase == "sqlite":
            sent = other.call("trw_send", recipient_member_id="impl-1", request_key="before-cancel", body="preserved")
            expected_groups = stable_state(path)["groups"]
            before["endpoints"] = stable_state(path)["endpoints"]  # the sender's own send renews ITS lease
            conn.execute("BEGIN IMMEDIATE")
            waiting.call("probe", trace=True)
            waiting.release()
            waiting.event("begin")  # sqlite is executing BEGIN IMMEDIATE under our held writer lock.
        waiting.cancel(request)
        waiting.event("cancelled")  # Original SDK cancellation has actually marked the worker's host scope.
        assert waiting.call("probe") == {"worker": True, "guard": True}
        if phase == "sleep":
            waiting.release()
        else:
            conn.rollback()
        assert_cancelled_exit(waiting)
        assert waiting.call("probe") == {"worker": False, "guard": False}
        assert_no_normal_response(waiting, request)
        after = stable_state(path)
        assert after["endpoints"] == before["endpoints"]
        assert after["refusal_counts"] == before["refusal_counts"]
        assert after["groups"] == expected_groups
        assert rows(path, "SELECT group_time FROM groups")[0][0] >= clock_before
        if phase == "sleep":
            assert after == before
        else:
            assert rows(path, "SELECT state,body FROM admissions") == [("pending", "preserved")]
            assert {row[0] for row in rows(path, "SELECT fact FROM milestones")} == {"admitted", "fetch_prepared"}
            assert rows(path, "SELECT charge FROM groups") == [(1,)]
            # The cancelled attempt committed preparation, not ACK or delivery.
            fetched = waiting.call("trw_inbox")
            assert fetched["items"] == [{**sent["receipt"], "body": "preserved"}]
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("native_sleep", [False, True], ids=["barrier", "unchanged-default-sleep"])
def test_real_transport_eof_cancels_wait_and_reaps_worker_without_response(pair: Any, native_sleep: bool) -> None:
    waiting, _other, path = pair
    before = stable_state(path)
    clock_before = rows(path, "SELECT group_time FROM groups")[0][0]
    request = begin_wait(waiting, native_sleep=native_sleep)
    before["endpoints"] = stable_state(path)["endpoints"]  # FR12: the ordinary first attempt renews
    proc = waiting.server.proc
    assert proc.stdin is not None
    proc.stdin.close()  # Real stdio EOF, not client task.cancel or a fake callback.
    waiting.event("cancelled")
    if not native_sleep:
        waiting.release()
    assert_cancelled_exit(waiting)
    assert proc.wait(timeout=15) == 0
    assert proc.stdout is not None
    waiting.frames.extend(json.loads(line) for line in proc.stdout.read().splitlines())
    assert_no_normal_response(waiting, request)
    assert stable_state(path) == before
    assert rows(path, "SELECT group_time FROM groups")[0][0] >= clock_before
