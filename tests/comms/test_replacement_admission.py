"""PRD-CORE-274 FR13 (Amendment 02): durable member-addressed admission and redelivery.

Supersedes the FR07 behaviour this module used to pin (a lapsed recipient refused; a
replacement expired the old incarnation's queue). Messages now belong to the MEMBER:
stored whatever the lease, surviving any takeover, delivered at least once with an
explicit redelivery flag, and expired only by their own deadline or a terminal member.
"""

from __future__ import annotations

import asyncio

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.conftest import call_peers
from tests.comms.test_policy import SendScene, scene  # noqa: F401


def _inbox(scene: SendScene, **arguments: object) -> dict[str, object]:
    result = asyncio.run(scene.server.call_tool("trw_inbox", arguments)).structured_content
    assert isinstance(result, dict)
    return result


def _takeover(scene: SendScene, member: str = "impl-2") -> None:
    """A new serving process for the same binding (e.g. /mcp reconnect) enrolls."""
    from trw_mcp.comms import _endpoints

    _endpoints._reset_process_incarnations_for_test()
    scene.actor(member)
    assert call_peers(scene.server, "enroll")["status"] == "ok"


def test_a_send_to_a_lapsed_member_is_stored_survives_takeover_and_is_redelivered(scene: SendScene) -> None:
    original = scene.send()
    assert original["status"] == "ok"
    scene.rows("UPDATE groups SET group_time=group_time+1000")  # impl-2's lease lapses
    later = scene.send("new")
    assert later["status"] == "ok", "FR13: a lapsed lease no longer refuses a direct send"
    assert later["recipient"]["availability"] == "idle"
    assert "recipient_availability" not in later["receipt"], "the receipt stays immutable provenance"

    _takeover(scene)
    assert scene.rows("SELECT state FROM admissions ORDER BY rowid") == [("pending",), ("pending",)]
    first = _inbox(scene, action="fetch")["items"]
    assert [item["body"] for item in first] == ["hello", "hello"]
    assert not any(item.get("redelivered") for item in first), "first preparation is not a redelivery"

    _takeover(scene)  # unACKed rows reach the next generation, flagged
    again = _inbox(scene, action="fetch")["items"]
    assert [item["message_id"] for item in again] == [item["message_id"] for item in first]
    assert all(item["redelivered"] is True for item in again)
    assert scene.rows("SELECT delivery_count FROM admissions ORDER BY rowid") == [(2,), (2,)]
    acked = _inbox(scene, action="ack", message_ids=[item["message_id"] for item in again])
    assert acked["status"] == "ok"

    scene.actor("impl-1")
    retried = scene.send()
    assert retried["receipt"] == original["receipt"], "an exact retry returns the original receipt"
    assert retried["recipient"]["generation"] == 3, "the observation beside it is fresh"
    assert scene.rows("SELECT charge FROM groups") == [(2,)]


def test_a_manifest_pin_change_does_not_block_a_direct_send(scene: SendScene) -> None:
    from trw_mcp import formation

    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"pin_key": "new-pin"}},
        trw_dir=scene.formation.trw_dir,
    )
    assert scene.send()["status"] == "ok", "the member, not its current endpoint binding, is addressed"
    assert scene.rows("SELECT charge FROM groups") == [(1,)]


def test_a_rebound_member_takes_over_at_once_and_the_old_binding_is_fenced(scene: SendScene) -> None:
    from tests._formation_test_support import write_pin
    from trw_mcp import formation

    assert scene.send()["status"] == "ok"
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"pin_key": "new-pin"}},
        trw_dir=scene.formation.trw_dir,
    )
    write_pin(scene.formation, "new-pin", scene.formation.member_runs["impl-2"])
    scene.monkeypatch.setenv("TRW_SESSION_ID", "new-pin")
    assert call_peers(scene.server, "enroll")["status"] == "ok", "no lease wait before the takeover"
    assert scene.rows("SELECT state FROM admissions") == [("pending",)], "the queue survives the takeover"
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"pin_key": "pin-b"}},
        trw_dir=scene.formation.trw_dir,
    )
    scene.actor("impl-2")
    assert call_peers(scene.server, "heartbeat")["reason"] == "endpoint_replaced_by_newer_incarnation"
    assert scene.rows("SELECT state FROM admissions") == [("pending",)]


def test_a_displaced_process_cannot_send_but_its_replacement_can(scene: SendScene) -> None:
    from trw_mcp.comms import _endpoints

    assert call_peers(scene.server, "enroll")["status"] == "ok"  # impl-1's first process
    old_process = dict(_endpoints._PROCESS_INCARNATIONS)
    _endpoints._reset_process_incarnations_for_test()
    assert call_peers(scene.server, "enroll")["status"] == "ok"  # impl-1's new process takes over
    new_process = dict(_endpoints._PROCESS_INCARNATIONS)
    _endpoints._PROCESS_INCARNATIONS.clear()
    _endpoints._PROCESS_INCARNATIONS.update(old_process)
    refused = scene.send("from-old")
    assert refused["reason"] == "endpoint_replaced_by_newer_incarnation"
    _endpoints._reset_process_incarnations_for_test()
    _endpoints._PROCESS_INCARNATIONS.update(new_process)
    assert scene.send("from-new")["status"] == "ok"


def test_rows_expire_at_their_deadline_and_are_never_fetched(scene: SendScene) -> None:
    assert scene.send()["status"] == "ok"
    ttl = scene.config.comms_message_ttl_seconds
    scene.rows("UPDATE groups SET group_time=group_time+?", (ttl + 1,))
    scene.actor("impl-2")
    assert _inbox(scene, action="fetch")["items"] == []
    assert scene.rows("SELECT state FROM admissions") == [("expired",)]
    assert {row[0] for row in scene.rows("SELECT fact FROM milestones")} == {"admitted", "expired"}


def test_a_terminal_members_rows_expire_and_are_never_retargeted(scene: SendScene) -> None:
    from trw_mcp import formation

    assert scene.send()["status"] == "ok"
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"status": "abandoned"}},
        trw_dir=scene.formation.trw_dir,
    )
    assert scene.send("after-terminal")["reason"] == "recipient_not_eligible"
    assert scene.rows("SELECT state, recipient_member_id FROM admissions") == [("expired", "impl-2")]


from typing import Any

import pytest


@pytest.mark.parametrize(
    # FR12: a takeover writes only the endpoint; the FR13 expiry sweep runs in every operation.
    "statement",
    ["INSERT OR IGNORE INTO milestones", "UPDATE admissions SET state", "INSERT INTO endpoints"],
)
def test_failure_after_each_replacement_write_preserves_old_endpoint_and_message(
    scene: SendScene, statement: str
) -> None:
    from trw_mcp.comms import _endpoints, _store

    assert scene.send()["status"] == "ok"
    scene.rows("UPDATE groups SET group_time=group_time+1000")
    tables = ("groups", "endpoints", "admissions", "milestones", "refusal_counts")
    before = {table: scene.rows(f"SELECT * FROM {table}") for table in tables}
    real_connect = _store.sqlite3.connect

    class Failing(_store.sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> Any:
            result = super().execute(sql, parameters)
            if sql.startswith(statement):
                raise _store.sqlite3.DatabaseError("injected after replacement write")
            return result

    def connect(*args: Any, **kwargs: Any) -> Any:
        return real_connect(*args, **kwargs, factory=Failing)

    _endpoints._reset_process_incarnations_for_test()
    scene.actor("impl-2")
    with scene.monkeypatch.context() as patch:
        patch.setattr(_store.sqlite3, "connect", connect)
        assert call_peers(scene.server, "enroll")["reason"] == "storage_corrupt"
    assert {table: scene.rows(f"SELECT * FROM {table}") for table in tables} == before


def test_peers_rows_report_advisory_availability_generation_and_unobserved_wake(scene: SendScene) -> None:
    _takeover(scene)  # impl-2 generation 2
    scene.rows("UPDATE groups SET group_time=group_time+1000")  # impl-1 and impl-2 leases lapse
    scene.actor("impl-1")
    rows = {row["member_id"]: row for row in call_peers(scene.server, "list")["peers"]}
    assert rows["impl-2"]["generation"] == 2
    assert rows["impl-2"]["availability"] == "idle", "lapsed but inside the delivery horizon"
    assert rows["impl-2"]["last_seen_seconds_ago"] >= 1000
    assert rows["impl-2"]["wake"] == "none", "never inferred from a client name"
