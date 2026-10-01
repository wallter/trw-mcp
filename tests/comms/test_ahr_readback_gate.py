"""PRD-CORE-349 FR03/FR04/NFR03: read-back before accept, the remaining actions, and the gate's cost.

Everything runs through the real ``trw_send``/``trw_inbox`` tool dispatch with two members:
impl-1 sends, impl-2 receives.
"""

from __future__ import annotations

import hashlib
import time

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms._ahr_support import call, events, handoff_doc, inbox, offer, read_back, readback_doc, write
from tests.comms.test_policy import SendScene, scene  # noqa: F401


def _kinds(s: SendScene, handoff_id: str) -> list[str]:
    return [event for _seq, event, _actor in events(s, handoff_id)]


def _facts(s: SendScene, message_id: str) -> set[str]:
    return {str(fact) for (fact,) in s.rows("SELECT fact FROM milestones WHERE message_id=?", (message_id,))}


def test_accept_without_a_read_back_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    refused = inbox(scene, "impl-2", "accept", message_id)
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_readback_required")
    assert _kinds(scene, handoff["handoff_id"]) == ["offered"]
    assert "accepted" not in _facts(scene, message_id)


def test_accept_after_a_questions_read_back_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert read_back(scene, message_id, readback_doc(handoff, disposition="questions"))["status"] == "ok"
    refused = inbox(scene, "impl-2", "accept", message_id)
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_readback_required")
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "read_back"]


def test_accept_over_a_contradicted_claim_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert read_back(scene, message_id, readback_doc(handoff, contradicted=True))["status"] == "ok"
    refused = inbox(scene, "impl-2", "accept", message_id)
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_readback_required")


def test_a_read_back_by_another_member_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    scene.actor("impl-1")
    assert call(scene, "trw_inbox", action="enroll")["status"] == "ok"  # the receiver fence needs an endpoint
    refused = read_back(scene, message_id, readback_doc(handoff, by="impl-1", check=False), member="impl-1")
    assert (refused["status"], refused["reason"]) == ("refused", "handoff_not_authorized")
    assert _kinds(scene, handoff["handoff_id"]) == ["offered"]


def test_a_read_back_naming_another_author_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    refused = read_back(scene, message_id, readback_doc(handoff, by="impl-1", check=False))
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert _kinds(scene, handoff["handoff_id"]) == ["offered"]


def test_ready_read_back_then_accept_report_complete(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    readback = readback_doc(handoff)
    assert read_back(scene, message_id, readback)["status"] == "ok"
    assert read_back(scene, message_id, readback)["status"] == "ok", "an exact read-back retry is a no-op"
    accepted = inbox(scene, "impl-2", "accept", message_id)
    assert accepted["status"] == "ok", accepted
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok", "an accept retry is a no-op"
    assert {"acked", "accepted"} <= _facts(scene, message_id)
    ((doc,),) = scene.rows("SELECT event_doc FROM ahr_events WHERE event='accepted'")
    assert b'"effective_at"' in bytes(doc)
    content = b"Suite: 812 passed (scope full).\n"
    result = write(scene.formation.project_root, "result.txt", content)
    bound = f"{result}#sha256:{hashlib.sha256(content).hexdigest()}"
    reported = inbox(scene, "impl-2", "report", message_id, next_read=bound, handoff={"outcome": "met"})
    assert reported["status"] == "ok", reported
    completed = inbox(scene, "impl-1", "complete", message_id)
    assert completed["status"] == "ok", completed
    assert "unresolved_next_read" not in completed
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "read_back", "accepted", "reported", "completed"]
    assert {"accepted", "reported", "completed"} <= _facts(scene, message_id)


def test_report_with_a_wrong_digest_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(handoff))
    inbox(scene, "impl-2", "accept", message_id)
    result = write(scene.formation.project_root, "result.txt", b"done\n")
    refused = inbox(
        scene, "impl-2", "report", message_id, next_read=f"{result}#sha256:{'0' * 64}", handoff={"outcome": "met"}
    )
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_ref_unverified")
    assert _kinds(scene, handoff["handoff_id"])[-1] == "accepted"


def test_the_receiver_cannot_complete(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    refused = inbox(scene, "impl-2", "complete", message_id)
    assert refused["status"] == "refused"
    assert _kinds(scene, handoff["handoff_id"]) == ["offered"]


def test_decline_ends_the_handoff_and_blocks_accept(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    declined = inbox(scene, "impl-2", "decline", message_id, handoff={"reason": "Out of my lane."})
    assert declined["status"] == "ok", declined
    read_back(scene, message_id, readback_doc(handoff))
    assert inbox(scene, "impl-2", "accept", message_id)["reason"] == "ahr_lifecycle_refused"
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "declined"]


def test_offer_withdraw_by_the_sender_only(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    refused = inbox(scene, "impl-2", "offer_withdraw", message_id, handoff={"reason": "Not mine."})
    assert refused["status"] == "refused"
    withdrawn = inbox(scene, "impl-1", "offer_withdraw", message_id, handoff={"reason": "Wrong lane."})
    assert withdrawn["status"] == "ok", withdrawn
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "withdrawn"]


def test_answer_by_the_sender_binds_a_local_file(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(handoff, disposition="questions"))
    note = write(scene.formation.project_root, "answer.md", b"The target is the scratch worktree.\n")
    answered = inbox(scene, "impl-1", "answer", message_id, handoff={"path": note})
    assert answered["status"] == "ok", answered
    assert read_back(scene, message_id, readback_doc(handoff, readback_id="RB-2", at=time.time() - 1))["status"] == "ok"
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "read_back", "answered", "read_back", "accepted"]


def test_a_minimal_record_accepts_without_a_read_back(scene: SendScene) -> None:
    handoff = handoff_doc(tier="minimal")
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "accepted"]


def test_non_ahr_requests_keep_the_core322_accept(scene: SendScene) -> None:
    sent = scene.send(key="plain", body="task")
    message_id = sent["receipt"]["message_id"]
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    assert scene.rows("SELECT COUNT(*) FROM ahr_events") == [(0,)]
    refused = inbox(scene, "impl-2", "read_back", message_id, handoff={"path": "x.json"})
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_not_offered")


def test_status_shows_a_body_free_ahr_block(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(handoff))
    scene.actor("impl-1")
    (item,) = call(scene, "trw_inbox", action="status")["items"]
    block = item["handoff"]["ahr"]
    assert block == {
        "handoff_id": handoff["handoff_id"],
        "tier": "standard",
        "state": "offered",
        "owner": "impl-1",
        "readback": "ready",
    }
    assert handoff["objective"]["goal"] not in str(item)


def _readback_ready_offer(scene: SendScene) -> str:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(handoff))
    return str(handoff["handoff_id"])


def test_the_timed_replay_rebuilds_the_offered_log(scene: SendScene) -> None:
    """The functional half of the NFR03 measurement (its timed half asserts only the budget)."""
    from trw_mcp.comms import _ahr_events, _store

    handoff_id = _readback_ready_offer(scene)
    conn = _store.sqlite3.connect(_store.database_path(scene.formation.manifest_path()))
    try:
        log = _ahr_events.rebuild(conn, handoff_id)
    finally:
        conn.close()
    assert (log.state, log.count, sorted(log.latest_rb)) == ("offered", 2, ["impl-2"])


@pytest.mark.requires_local_timing
def test_the_accept_gate_p95_is_under_100ms(scene: SendScene) -> None:
    from tests._timing import assert_budget
    from trw_mcp.comms import _ahr_events, _store

    handoff_id = _readback_ready_offer(scene)
    conn = _store.sqlite3.connect(_store.database_path(scene.formation.manifest_path()))
    try:
        samples = []
        for _ in range(50):
            start = time.perf_counter()
            _ahr_events.rebuild(conn, handoff_id)
            samples.append(time.perf_counter() - start)
    finally:
        conn.close()
    samples.sort()
    assert_budget("ahr_accept_gate_replay_p95", samples[int(0.95 * len(samples)) - 1], 0.1, "s")
