"""PRD-CORE-349 FR05/FR07: supersession (R-SUP-2/R-SUP-6), the fork display (R-SUP-3) and the board block."""

from __future__ import annotations

import sqlite3
import time

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms._ahr_support import call, events, handoff_doc, inbox, offer, read_back, readback_doc
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.handoff import digest


def _kinds(s: SendScene, handoff_id: str) -> list[str]:
    return [event for _seq, event, _actor in events(s, handoff_id)]


def _successor(first: dict[str, object], **changes: object) -> dict[str, object]:
    listed = [{"handoff_id": first["handoff_id"], "digest": digest(first)}]  # type: ignore[arg-type]
    return handoff_doc(handoff_id="01J9ZK5W1STD000000000002", supersedes=listed, **changes)  # type: ignore[arg-type]


def test_a_superseding_offer_appends_superseded_then_offered(scene: SendScene) -> None:
    first = handoff_doc()
    offer(scene, first, key="v1")
    second = _successor(first)
    sent = offer(scene, second, key="v2")
    assert sent["status"] == "ok", sent
    assert _kinds(scene, str(first["handoff_id"])) == ["offered", "superseded"]
    assert _kinds(scene, str(second["handoff_id"])) == ["offered"]
    ((ref,),) = scene.rows("SELECT event_doc FROM ahr_events WHERE event='superseded'")
    assert f"trw:ahr/{second['handoff_id']}".encode() in bytes(ref)


def test_the_superseded_record_stays_in_the_log_and_refuses_accept(scene: SendScene) -> None:
    first = handoff_doc()
    message_id = offer(scene, first, key="v1")["receipt"]["message_id"]
    offer(scene, _successor(first), key="v2")
    refused = read_back(scene, message_id, readback_doc(first))
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert scene.rows("SELECT COUNT(*) FROM ahr_events WHERE handoff_id=?", (first["handoff_id"],)) == [(2,)]


@pytest.mark.parametrize("changes", [{"tier": "minimal"}, {"subject": "another-subject"}])
def test_a_lower_tier_or_another_subject_cannot_supersede(scene: SendScene, changes: dict[str, str]) -> None:
    first = handoff_doc()
    offer(scene, first, key="v1")
    refused = offer(scene, _successor(first, **changes), key="v2")
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert _kinds(scene, str(first["handoff_id"])) == ["offered"]
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)], "the refused carrier was rolled back too"


def test_an_accepted_record_cannot_be_superseded(scene: SendScene) -> None:
    first = handoff_doc()
    message_id = offer(scene, first, key="v1")["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(first))
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    refused = offer(scene, _successor(first), key="v2")
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert _kinds(scene, str(first["handoff_id"]))[-1] == "accepted"


@pytest.mark.parametrize("first_writer", ["accept", "supersede"])
def test_accept_and_supersede_serialize_the_first_appended_event_wins(scene: SendScene, first_writer: str) -> None:
    """R-SUP-6: both writers target one offered record; whichever is appended first wins, the other is refused.

    Every comms write holds the mailbox's BEGIN IMMEDIATE lock (the next test proves a held lock
    turns the second writer away), so the two orders are the only interleavings there are.
    """
    first = handoff_doc()
    message_id = offer(scene, first, key="v1")["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(first))
    second = _successor(first)
    if first_writer == "accept":
        winner, loser = inbox(scene, "impl-2", "accept", message_id), offer(scene, second, key="v2")
    else:
        winner, loser = offer(scene, second, key="v2"), inbox(scene, "impl-2", "accept", message_id)
    assert winner["status"] == "ok", winner
    assert (loser["status"], loser["reason"]) == ("refused", "ahr_lifecycle_refused")
    kinds = _kinds(scene, str(first["handoff_id"]))
    assert kinds.count("accepted") + kinds.count("superseded") == 1
    assert kinds[-1] == ("accepted" if first_writer == "accept" else "superseded")


def test_the_store_lock_serializes_concurrent_writers(scene: SendScene) -> None:
    """The AHR append runs inside the facade's BEGIN IMMEDIATE: a second writer waits or is refused."""
    first = handoff_doc()
    offer(scene, first, key="v1")
    from trw_mcp.comms import _store

    holder = sqlite3.connect(_store.database_path(scene.formation.manifest_path()), timeout=0.1)
    holder.execute("BEGIN IMMEDIATE")
    try:
        start = time.monotonic()
        refused = offer(scene, _successor(first), key="v2")
        assert refused["status"] == "refused" and refused["reason"] == "storage_contended"
        assert time.monotonic() - start < 30
    finally:
        holder.rollback()
        holder.close()
    assert _kinds(scene, str(first["handoff_id"])) == ["offered"]


def test_two_current_records_for_one_subject_show_a_fork(scene: SendScene) -> None:
    first = handoff_doc()
    offer(scene, first, key="v1")
    other = handoff_doc(handoff_id="01J9ZK5W1STD000000000009")  # same subject, does not list the first
    assert offer(scene, other, key="v9")["status"] == "ok"
    scene.actor("impl-1")
    items = call(scene, "trw_inbox", action="status")["items"]
    assert [item["handoff"]["ahr"].get("fork") for item in items] == [True, True]


def test_the_trw_status_board_carries_the_ahr_block(scene: SendScene) -> None:
    from trw_mcp.comms._handoff import open_handoffs
    from trw_mcp.comms._identity import derive_group_id
    from trw_mcp.comms._store import database_path

    first = handoff_doc()
    offer(scene, first, key="v1")
    manifest = scene.formation.manifest_path()
    conn = sqlite3.connect(database_path(manifest).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        group_id = derive_group_id(scene.formation.project_root, manifest)
        listed, omitted = open_handoffs(conn, group_id, now=time.time(), ttl_seconds=86400)
    finally:
        conn.close()
    assert omitted == 0
    (entry,) = listed
    assert entry["ahr"] == {
        "handoff_id": first["handoff_id"],
        "tier": "standard",
        "state": "offered",
        "owner": "impl-1",
    }
