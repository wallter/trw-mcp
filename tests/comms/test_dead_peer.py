"""PRD-CORE-274 FR12 (Amendment 02; supersedes the FR07 lease gate): generation
takeover, permanent fencing of a displaced process, and renewal.

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
    saved_displaced = set(_endpoints._DISPLACED)
    _endpoints._PROCESS_INCARNATIONS.clear()
    _endpoints._DISPLACED.clear()
    try:
        yield
    finally:
        _endpoints._PROCESS_INCARNATIONS.clear()
        _endpoints._PROCESS_INCARNATIONS.update(saved)
        _endpoints._DISPLACED.clear()
        _endpoints._DISPLACED.update(saved_displaced)


@pytest.fixture
def enrolled(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> FormationFixture:
    joined_member(formation_env, "impl-1", "pin-a")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    enable_comms(monkeypatch)
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    return formation_env


def _generation(fixture: FormationFixture) -> int:
    conn = sqlite3.connect(_db(fixture))
    try:
        return int(conn.execute("SELECT generation FROM endpoints WHERE member_id='impl-1'").fetchone()[0])
    finally:
        conn.close()


def test_a_process_that_never_held_the_endpoint_takes_it_over_without_waiting(
    comms_server: FastMCP, enrolled: FormationFixture
) -> None:
    """FR12: the binding (manifest run AND pin) is the authority, not the lease. A new
    process for the same binding -- a /mcp reconnect -- takes over at once."""
    assert _generation(enrolled) == 1
    with other_process():
        payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "ok"
    assert _generation(enrolled) == 2


def test_expired_endpoint_admits_a_replacement(comms_server: FastMCP, enrolled: FormationFixture) -> None:
    """Once the lease lapses the endpoint is claimable — that is its purpose."""
    advance_group_clock(enrolled, 10_000)

    with other_process():
        payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "ok"
    assert payload["member_id"] == "impl-1"


def test_a_displaced_process_is_fenced_permanently_so_there_is_no_ping_pong(
    comms_server: FastMCP, enrolled: FormationFixture
) -> None:
    """FR12: once taken over, the old process is refused for enroll too, with the recovery named."""
    with other_process():
        assert call_peers(comms_server, "enroll")["status"] == "ok"
    for action in ("heartbeat", "enroll", "enroll"):
        payload = call_peers(comms_server, action)
        assert payload["reason"] == "endpoint_replaced_by_newer_incarnation", (action, payload)
        assert "reconnect" in payload["detail"]
    assert _generation(enrolled) == 2, "the displaced process never took the endpoint back"


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


def test_the_displacement_guard_is_load_bearing(
    comms_server: FastMCP, enrolled: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEGATIVE CONTROL: without ``_assert_not_displaced`` the displaced process renews an
    endpoint it no longer owns. Disabled by monkeypatch in this process only."""
    with other_process():
        assert call_peers(comms_server, "enroll")["status"] == "ok"
    assert call_peers(comms_server, "heartbeat")["reason"] == "endpoint_replaced_by_newer_incarnation"  # guarded
    monkeypatch.setattr(_endpoints, "_assert_not_displaced", lambda *a, **k: None)
    _endpoints._DISPLACED.clear()
    assert call_peers(comms_server, "heartbeat")["status"] == "ok"  # unguarded: the fence is gone
