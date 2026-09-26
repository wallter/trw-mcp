"""CORE274 successive real processes: closure, receiver collision and replacement."""

from __future__ import annotations

from typing import Any

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_crash_window import crash_scene, driver_factory  # noqa: F401
from tests.comms.test_policy import SendScene


def test_irreversible_closure_survives_reactivation_and_process_restart(
    crash_scene: SendScene, driver_factory: Any
) -> None:
    from trw_mcp import formation

    s = crash_scene
    receiver = driver_factory("pin-b")
    assert receiver.call("trw_inbox", action="enroll")["status"] == "ok"
    sender = driver_factory("pin-a")
    args = {"recipient_member_id": "impl-2", "request_key": "retained", "body": "retained"}
    original = sender.call("trw_send", **args)
    assert original["status"] == "ok"
    formation.revise(
        "release-train",
        s.formation.orchestrator_run,
        {"impl-1": {"status": "abandoned"}, "impl-2": {"status": "abandoned"}},
        trw_dir=s.formation.trw_dir,
    )
    assert receiver.call("trw_inbox", cursor="malformed")["reason"] == "group_closed"
    assert s.rows("SELECT closed FROM groups") == [(1,)]
    receiver.close()
    sender.close()
    assert receiver.child.returncode == sender.child.returncode == 0
    formation.revise(
        "release-train",
        s.formation.orchestrator_run,
        {"impl-1": {"status": "active"}, "impl-2": {"status": "active"}},
        trw_dir=s.formation.trw_dir,
    )
    restarted = driver_factory("pin-a")
    retried = restarted.call("trw_send", **args)
    assert retried["receipt"] == original["receipt"]
    assert retried["message_state"] == "expired", "closure expired it; the retry reports that (FR15)"
    # Every canonical field is still compared to retained admission after a
    # real restart, before fresh closed-group admission can mask the conflict.
    before_conflicts = s.rows("SELECT * FROM admissions"), s.rows("SELECT charge FROM groups")
    for changed in (
        {"recipient_member_id": "impl-1"},
        {"body": "different"},
        {"kind": "reply"},
        {"delivery_class": "interrupt"},
    ):
        assert restarted.call("trw_send", **{**args, **changed})["reason"] == "idempotency_conflict"
    assert (s.rows("SELECT * FROM admissions"), s.rows("SELECT charge FROM groups")) == before_conflicts
    assert restarted.call("trw_send", **{**args, "request_key": "new"})["reason"] == "group_closed"
    status = restarted.call("trw_inbox", action="status")
    assert status["status"] == "ok" and len(status["items"]) == 1
    assert "body" not in status["items"][0]
    assert restarted.call("trw_inbox")["reason"] == "group_closed"
    assert s.rows("SELECT closed,charge FROM groups") == [(1, 1)]
    restarted.send(verify=True)
    assert restarted.receive("VERIFIED")["status"]["status"] == "ok"


def test_real_process_takeover_fences_the_old_process_and_the_queue_survives(
    crash_scene: SendScene, driver_factory: Any
) -> None:
    """PRD-CORE-274 FR12/FR13 across real stdio server processes (supersedes the FR07
    collision-then-expiry lifecycle): a new process for the same binding takes over at
    once, the displaced one is refused, and the message admitted before is delivered."""
    s = crash_scene
    old_receiver = driver_factory("pin-b")
    assert old_receiver.call("trw_inbox", action="enroll")["status"] == "ok"
    old_incarnation = s.rows("SELECT incarnation FROM endpoints")[0][0]
    sender = driver_factory("pin-a")
    args = {"recipient_member_id": "impl-2", "request_key": "retained", "body": "old traffic"}
    original = sender.call("trw_send", **args)
    message_id = original["receipt"]["message_id"]
    replacement = driver_factory("pin-b")
    assert replacement.call("trw_inbox", action="enroll")["status"] == "ok", "no lease wait"
    assert s.rows("SELECT incarnation FROM endpoints")[0][0] != old_incarnation
    assert s.rows("SELECT generation FROM endpoints") == [(2,)]
    assert s.rows("SELECT state FROM admissions") == [("pending",)], "replacement expires nothing"
    assert old_receiver.call("trw_inbox", action="heartbeat")["reason"] == "endpoint_replaced_by_newer_incarnation"
    assert old_receiver.call("trw_inbox")["reason"] == "endpoint_replaced_by_newer_incarnation"
    assert (
        old_receiver.call("trw_inbox", action="ack", message_ids=[message_id])["reason"]
        == "endpoint_replaced_by_newer_incarnation"
    )
    assert old_receiver.call("trw_inbox", action="enroll")["reason"] == "endpoint_replaced_by_newer_incarnation"
    delivered = replacement.call("trw_inbox")["items"]
    assert delivered == [{**original["receipt"], "body": "old traffic"}], "first preparation: no redelivery flag"
    assert replacement.call("trw_inbox", action="ack", message_ids=[message_id])["status"] == "ok"
    assert sender.call("trw_send", **args)["receipt"] == original["receipt"]
    assert s.rows("SELECT charge FROM groups") == [(1,)]
    fresh = sender.call("trw_send", **{**args, "request_key": "new", "body": "new traffic"})
    assert fresh["status"] == "ok"
    assert replacement.call("trw_inbox")["items"] == [{**fresh["receipt"], "body": "new traffic"}]
    assert s.rows("SELECT charge FROM groups") == [(2,)]
    replacement.close()
    old_receiver.close()
    sender.close()
    assert replacement.child.returncode == old_receiver.child.returncode == sender.child.returncode == 0
    verifier = driver_factory("pin-a")
    verifier.send(verify=True)
    state = verifier.receive("VERIFIED")
    assert state["status"]["status"] == "ok"
    assert [item["state"] for item in state["status"]["items"]] == ["acked", "pending"]
