"""PRD-CORE-274-FR07: leases, incarnation fencing, replacement and renewal.

Elapsed time is simulated by advancing the PERSISTED group clock rather than by
sleeping or by patching ``time.time``. That is the same clock the product reads
(``effective_time`` is ``max(wall clock, persisted)``), so these tests exercise
the real expiry path instead of a mocked one, and they finish instantly.

A "different process" is simulated by clearing this process's incarnation
memory, which is exactly what a restart does: same database, no in-memory
claims. There is no other way to become a different incarnation, because a
caller cannot name one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms, joined_member
from trw_mcp.comms import _endpoints
from trw_mcp.comms._store import database_path


def _db(fixture: FormationFixture) -> Path:
    return database_path(fixture.manifest_path())


def advance_group_clock(fixture: FormationFixture, seconds: float) -> None:
    """Push the persisted group clock forward, as real elapsed time would."""
    conn = sqlite3.connect(_db(fixture))
    try:
        conn.execute("UPDATE groups SET group_time = group_time + ?", (seconds,))
        conn.commit()
    finally:
        conn.close()


@contextmanager
def other_process() -> Iterator[None]:
    """Run the body as a process that holds none of this one's incarnations."""
    saved = dict(_endpoints._PROCESS_INCARNATIONS)
    _endpoints._PROCESS_INCARNATIONS.clear()
    try:
        yield
    finally:
        _endpoints._PROCESS_INCARNATIONS.clear()
        _endpoints._PROCESS_INCARNATIONS.update(saved)


@pytest.fixture
def enrolled(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> FormationFixture:
    joined_member(formation_env, "impl-1", "pin-a")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    enable_comms(monkeypatch)
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    return formation_env


def test_live_endpoint_refuses_a_different_incarnation(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """Two live processes claiming one member is a conflict, not a race to win."""
    with other_process():
        payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "refused"
    assert payload["reason"] == "live_endpoint_held_by_other_incarnation"


def test_expired_endpoint_admits_a_replacement(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """Once the lease lapses the endpoint is claimable — that is its purpose."""
    advance_group_clock(enrolled, 10_000)

    with other_process():
        payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "ok"
    assert payload["member_id"] == "impl-1"


def test_replaced_incarnation_is_fenced_out(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """The displaced process must LEARN it lost, not write on regardless.

    This is the case the whole mechanism exists for: a peer that stalled past
    its lease, was replaced, and then came back. Silently accepting its
    heartbeat would leave two processes believing they own one mailbox.
    """
    advance_group_clock(enrolled, 10_000)
    with other_process():
        assert call_peers(comms_server, "enroll")["status"] == "ok"

    payload = call_peers(comms_server, "heartbeat")

    assert payload["status"] == "refused"
    assert payload["reason"] == "endpoint_replaced_by_newer_incarnation"


def test_same_incarnation_renews_an_expired_lease(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """Expiry alone does not mean gone — only that it stopped saying so.

    With no replacement in between, the original process renews and carries on.
    """
    advance_group_clock(enrolled, 10_000)

    payload = call_peers(comms_server, "heartbeat")

    assert payload["status"] == "ok"
    assert payload["lease_expires_in_seconds"] > 0
    assert payload["peers"][0]["live"] is True


def test_heartbeat_without_enrolment_refuses(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness is explicit: heartbeat never creates the endpoint it renews."""
    joined_member(formation_env, "impl-1", "pin-a")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    enable_comms(monkeypatch)

    payload = call_peers(comms_server, "heartbeat")

    assert payload["status"] == "refused"
    assert payload["reason"] == "no_endpoint_for_member"


def test_listing_does_not_enroll_or_renew(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """Reading the roster is not a liveness signal.

    If listing renewed the lease, an agent that polls would look alive forever
    without ever claiming to be — which is precisely the lie the lease exists
    to prevent.
    """
    before = call_peers(comms_server, "list")["peers"][0]["lease_expires_in_seconds"]
    advance_group_clock(enrolled, 30)
    after = call_peers(comms_server, "list")["peers"][0]["lease_expires_in_seconds"]

    assert after < before - 29


def test_backwards_clock_cannot_revive_an_expired_lease(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """A clock that moves backwards must not resurrect a lapsed endpoint.

    The group clock is max(wall clock, persisted), so pushing the PERSISTED
    value far forward is indistinguishable, from the store's side, from the wall
    clock being dragged far back. The endpoint stays expired either way.
    """
    advance_group_clock(enrolled, 10_000)

    peers = call_peers(comms_server, "list")["peers"]

    assert peers[0]["live"] is False
    assert peers[0]["lease_expires_in_seconds"] < 0


@pytest.mark.parametrize(
    ("guard", "scenario", "reason"),
    [
        ("_assert_no_live_collision", "collision", "live_endpoint_held_by_other_incarnation"),
        ("_assert_not_fenced", "fenced", "endpoint_replaced_by_newer_incarnation"),
    ],
)
def test_fencing_guards_are_load_bearing(
    comms_server: FastMCP,
    enrolled: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
    guard: str,
    scenario: str,
    reason: str,
) -> None:
    """NEGATIVE CONTROL: each refusal above disappears without its own guard.

    Disabled by monkeypatch in this process only — never by editing the shared
    production file, which would leave a disarmed check on disk for every other
    agent working in this checkout.
    """
    if scenario == "collision":
        with other_process():
            assert call_peers(comms_server, "enroll")["reason"] == reason  # guarded
            monkeypatch.setattr(_endpoints, guard, lambda *a, **k: None)
            assert call_peers(comms_server, "enroll")["status"] == "ok"  # unguarded
        return

    advance_group_clock(enrolled, 10_000)
    with other_process():
        assert call_peers(comms_server, "enroll")["status"] == "ok"
    assert call_peers(comms_server, "heartbeat")["reason"] == reason  # guarded
    monkeypatch.setattr(_endpoints, guard, lambda *a, **k: None)
    assert call_peers(comms_server, "heartbeat")["status"] == "ok"  # unguarded
