"""CORE274 recipient binding and atomic replacement expiry without refund."""

from __future__ import annotations

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.conftest import call_peers
from tests.comms.test_policy import SendScene, scene  # noqa: F401


def test_lease_lapse_preserves_then_replacement_expires_and_retry_survives(scene: SendScene) -> None:
    from trw_mcp.comms import _endpoints

    original = scene.send()
    assert original["status"] == "ok"
    scene.rows("UPDATE groups SET group_time=group_time+1000")
    assert scene.rows("SELECT state FROM admissions") == [("pending",)]
    assert scene.send("new")["reason"] == "recipient_unavailable"
    _endpoints._reset_process_incarnations_for_test()
    scene.actor("impl-2")
    assert call_peers(scene.server, "enroll")["status"] == "ok"
    assert scene.rows("SELECT state FROM admissions") == [("expired",)]
    assert {row[0] for row in scene.rows("SELECT fact FROM milestones")} == {"admitted", "expired"}
    scene.actor("impl-1")
    assert scene.send() == original
    assert scene.send("after")["status"] == "ok"
    assert scene.rows("SELECT charge FROM groups") == [(2,)]


def test_manifest_recipient_binding_change_refuses_old_endpoint(scene: SendScene) -> None:
    from trw_mcp import formation

    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"pin_key": "new-pin"}},
        trw_dir=scene.formation.trw_dir,
    )
    assert scene.send()["reason"] == "recipient_binding_mismatch"
    assert scene.rows("SELECT charge FROM groups") == [(0,)]


def test_changed_binding_replaces_only_after_expiry_and_old_binding_stays_fenced(scene: SendScene) -> None:
    from tests._formation_test_support import write_pin
    from trw_mcp import formation

    assert scene.send()["status"] == "ok"
    original = scene.rows("SELECT incarnation FROM endpoints")[0][0]
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"pin_key": "new-pin"}},
        trw_dir=scene.formation.trw_dir,
    )
    write_pin(scene.formation, "new-pin", scene.formation.member_runs["impl-2"])
    scene.monkeypatch.setenv("TRW_SESSION_ID", "new-pin")
    assert call_peers(scene.server, "heartbeat")["reason"] == "endpoint_replaced_by_newer_incarnation"
    assert call_peers(scene.server, "enroll")["reason"] == "live_endpoint_held_by_other_incarnation"
    scene.rows("UPDATE groups SET group_time=group_time+1000")
    assert call_peers(scene.server, "enroll")["status"] == "ok"
    assert scene.rows("SELECT incarnation FROM endpoints")[0][0] != original
    assert scene.rows("SELECT state FROM admissions") == [("expired",)]
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"pin_key": "pin-b"}},
        trw_dir=scene.formation.trw_dir,
    )
    scene.actor("impl-2")
    assert call_peers(scene.server, "heartbeat")["reason"] == "endpoint_replaced_by_newer_incarnation"
    assert scene.rows("SELECT state FROM admissions") == [("expired",)]


def test_fenced_receiver_process_can_still_send_as_authorized_member(scene: SendScene) -> None:
    from trw_mcp.comms import _endpoints

    assert call_peers(scene.server, "enroll")["status"] == "ok"
    old_process = dict(_endpoints._PROCESS_INCARNATIONS)
    scene.rows("UPDATE groups SET group_time=group_time+1000")
    _endpoints._reset_process_incarnations_for_test()
    assert call_peers(scene.server, "enroll")["status"] == "ok"
    _endpoints._PROCESS_INCARNATIONS.clear()
    _endpoints._PROCESS_INCARNATIONS.update(old_process)
    assert call_peers(scene.server, "heartbeat")["reason"] == "endpoint_replaced_by_newer_incarnation"
    scene.actor("impl-2")
    assert call_peers(scene.server, "heartbeat")["status"] == "ok"
    scene.actor("impl-1")
    assert scene.send()["status"] == "ok"


from typing import Any

import pytest


@pytest.mark.parametrize(
    "statement", ["INSERT INTO milestones", "UPDATE admissions SET state", "INSERT INTO endpoints"]
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
