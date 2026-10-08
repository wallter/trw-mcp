"""PRD-CORE-349 follow-ups: onward and escalated reports (R-LC-12, R-TIER-5), answer retries, ended handoffs.

Everything runs through the real ``trw_send``/``trw_inbox`` tool dispatch with two members:
impl-1 sends, impl-2 receives and may hand the work onward.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms._ahr_support import (
    VECTOR,
    call,
    events,
    handoff_doc,
    inbox,
    offer,
    read_back,
    readback_doc,
    stamp,
    write,
)
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.handoff import digest, load, seal, validate

ONWARD = VECTOR.parent / "standard-escalated-to-critical" / "onward.json"


def _kinds(s: SendScene, handoff_id: str) -> list[str]:
    return [event for _seq, event, _actor in events(s, handoff_id)]


def _accepted(s: SendScene) -> tuple[dict[str, Any], str]:
    """A standard handoff impl-1 -> impl-2, read back and accepted; returns (record, carrier message id)."""
    handoff = handoff_doc()
    message_id = offer(s, handoff)["receipt"]["message_id"]
    read_back(s, message_id, readback_doc(handoff))
    assert inbox(s, "impl-2", "accept", message_id)["status"] == "ok"
    return handoff, message_id


def _critical_onward(original: dict[str, Any]) -> dict[str, Any]:
    """impl-2's critical escalation of *original* to a named human, listing it in supersedes (R-LC-12)."""
    doc = load(ONWARD)
    doc.pop("integrity", None)
    created = time.time() - 60
    doc["from"] = {"id": "impl-2", "kind": "agent"}
    doc["to"] = {"id": "oncall-7", "kind": "human"}
    doc["readback"]["verifier"] = {"id": "impl-1", "kind": "agent"}
    doc["subject"] = original["subject"]
    doc["supersedes"] = [{"handoff_id": original["handoff_id"], "digest": digest(original)}]
    doc["created_at"] = stamp(created)
    doc["as_of"]["at"] = stamp(created - 30)
    doc["expires_at"] = stamp(created + 3600)
    for action in doc["next_actions"]:
        action["owner"] = "oncall-7"
    sealed = seal(doc)
    assert validate(sealed) == [], validate(sealed)
    return sealed


def _bound(root: Path, name: str, doc: dict[str, Any]) -> str:
    """Write *doc* and return the §14 report pointer ``<path>#sha256:<raw-byte hex>``."""
    path = write(root, name, doc)
    return f"{path}#sha256:{hashlib.sha256((root / path).read_bytes()).hexdigest()}"


def test_report_returned_with_an_offered_onward_handoff_is_admitted(scene: SendScene) -> None:
    original, message_id = _accepted(scene)
    onward = handoff_doc(
        sender="impl-2",
        receiver="impl-1",
        handoff_id="01J9ZK5W1STD000000000002",
        supersedes=[{"handoff_id": original["handoff_id"], "digest": digest(original)}],
    )
    scene.actor("impl-2")
    sent = call(
        scene,
        "trw_send",
        recipient_member_id="impl-1",
        request_key="onward",
        body="",
        handoff={"path": write(scene.formation.project_root, "onward.json", onward)},
    )
    assert sent["status"] == "ok", sent
    pointer = _bound(scene.formation.project_root, "onward-copy.json", onward)
    reported = inbox(scene, "impl-2", "report", message_id, next_read=pointer, handoff={"outcome": "returned"})
    assert reported["status"] == "ok", reported
    assert _kinds(scene, original["handoff_id"])[-1] == "reported"
    ((ref,),) = scene.rows("SELECT event_doc FROM ahr_events WHERE event='reported'")
    stored = json.loads(bytes(ref))["ref"]
    assert (stored["media_type"], stored["digest"]) == ("application/vnd.ahr+json", digest(onward))
    scene.actor("impl-1")
    items = call(scene, "trw_inbox", action="status")["items"]
    states = {item["handoff"]["ahr"]["handoff_id"]: item["handoff"]["ahr"]["state"] for item in items}
    assert states[original["handoff_id"]] == "reported", "the stored onward ref must replay"
    assert inbox(scene, "impl-1", "complete", message_id)["status"] == "ok"


def test_report_with_an_onward_handoff_not_offered_here_is_refused(scene: SendScene) -> None:
    original, message_id = _accepted(scene)
    onward = handoff_doc(
        sender="impl-2",
        receiver="impl-1",
        handoff_id="01J9ZK5W1STD000000000003",
        supersedes=[{"handoff_id": original["handoff_id"], "digest": digest(original)}],
    )
    pointer = _bound(scene.formation.project_root, "onward.json", onward)
    refused = inbox(scene, "impl-2", "report", message_id, next_read=pointer, handoff={"outcome": "returned"})
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_onward_not_offered")
    assert _kinds(scene, original["handoff_id"])[-1] == "accepted"


def test_report_escalated_to_a_critical_handoff_is_admitted(scene: SendScene) -> None:
    original, message_id = _accepted(scene)
    pointer = _bound(scene.formation.project_root, "escalation.json", _critical_onward(original))
    reported = inbox(scene, "impl-2", "report", message_id, next_read=pointer, handoff={"outcome": "escalated"})
    assert reported["status"] == "ok", reported
    assert _kinds(scene, original["handoff_id"])[-1] == "reported"
    assert inbox(scene, "impl-1", "complete", message_id)["status"] == "ok", "the escalated log must replay"


def test_report_escalated_without_a_critical_handoff_is_refused(scene: SendScene) -> None:
    original, message_id = _accepted(scene)
    content = b"Escalating: the next step deletes shared state.\n"
    result = write(scene.formation.project_root, "why.txt", content)
    pointer = f"{result}#sha256:{hashlib.sha256(content).hexdigest()}"
    refused = inbox(scene, "impl-2", "report", message_id, next_read=pointer, handoff={"outcome": "escalated"})
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert _kinds(scene, original["handoff_id"])[-1] == "accepted"


def test_an_answer_retry_with_the_same_file_appends_one_event(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    read_back(scene, message_id, readback_doc(handoff, disposition="questions"))
    note = write(scene.formation.project_root, "answer.md", b"The target is the scratch worktree.\n")
    for _attempt in range(2):
        assert inbox(scene, "impl-1", "answer", message_id, handoff={"path": note})["status"] == "ok"
    assert _kinds(scene, handoff["handoff_id"]) == ["offered", "read_back", "answered"]
    other = write(scene.formation.project_root, "answer-2.md", b"Also: keep the launcher untouched.\n")
    assert inbox(scene, "impl-1", "answer", message_id, handoff={"path": other})["status"] == "ok"
    assert _kinds(scene, handoff["handoff_id"])[-1] == "answered"
    assert _kinds(scene, handoff["handoff_id"]).count("answered") == 2, "a different answer is a new event"


def _board(s: SendScene) -> list[dict[str, Any]]:
    from trw_mcp.comms._handoff import open_handoffs
    from trw_mcp.comms._identity import derive_group_id
    from trw_mcp.comms._store import database_path

    manifest = s.formation.manifest_path()
    conn = sqlite3.connect(database_path(manifest).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        group_id = derive_group_id(s.formation.project_root, manifest)
        listed, _omitted = open_handoffs(conn, group_id, now=time.time(), ttl_seconds=86400)
    finally:
        conn.close()
    return listed


@pytest.mark.parametrize(
    ("member", "action", "argument"),
    [("impl-2", "decline", {"reason": "Out of my lane."}), ("impl-1", "offer_withdraw", {"reason": "Wrong lane."})],
)
def test_an_ended_handoff_leaves_the_open_board(
    scene: SendScene, member: str, action: str, argument: dict[str, str]
) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert [entry["message_id"] for entry in _board(scene)] == [message_id]
    assert inbox(scene, member, action, message_id, handoff=argument)["status"] == "ok"
    assert _board(scene) == []


def test_a_superseded_handoff_leaves_the_open_board(scene: SendScene) -> None:
    first = handoff_doc()
    offer(scene, first, key="v1")
    second = handoff_doc(
        handoff_id="01J9ZK5W1STD000000000004",
        supersedes=[{"handoff_id": first["handoff_id"], "digest": digest(first)}],
    )
    second_id = offer(scene, second, key="v2")["receipt"]["message_id"]
    assert [entry["message_id"] for entry in _board(scene)] == [second_id]


def test_report_met_with_an_onward_handoff_is_refused(scene: SendScene) -> None:
    """rc.2 (A25-1): passing work on is `returned` or `escalated`, never `met`."""
    original, message_id = _accepted(scene)
    onward = handoff_doc(
        sender="impl-2",
        receiver="impl-1",
        handoff_id="01J9ZK5W1STD000000000005",
        supersedes=[{"handoff_id": original["handoff_id"], "digest": digest(original)}],
    )
    scene.actor("impl-2")
    path = write(scene.formation.project_root, "onward.json", onward)
    call(scene, "trw_send", recipient_member_id="impl-1", request_key="onward", body="", handoff={"path": path})
    pointer = _bound(scene.formation.project_root, "onward-copy.json", onward)
    refused = inbox(scene, "impl-2", "report", message_id, next_read=pointer, handoff={"outcome": "met"})
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert _kinds(scene, original["handoff_id"])[-1] == "accepted"


def test_an_onward_record_listing_an_accepted_one_is_not_a_fork(scene: SendScene) -> None:
    """R-SUP-1/R-LC-14: the listed predecessor keeps its duties but is no longer current."""
    original, _message_id = _accepted(scene)
    onward = handoff_doc(
        sender="impl-2",
        receiver="impl-1",
        handoff_id="01J9ZK5W1STD000000000006",
        supersedes=[{"handoff_id": original["handoff_id"], "digest": digest(original)}],
    )
    scene.actor("impl-2")
    path = write(scene.formation.project_root, "onward.json", onward)
    assert (
        call(scene, "trw_send", recipient_member_id="impl-1", request_key="onward", body="", handoff={"path": path})[
            "status"
        ]
        == "ok"
    )
    items = call(scene, "trw_inbox", action="status")["items"]
    assert [item["handoff"]["ahr"].get("fork") for item in items] == [None, None]


def test_an_uncanonical_onward_record_is_refused_as_invalid(scene: SendScene) -> None:
    original, message_id = _accepted(scene)
    bad = dict(_critical_onward(original), extensions={"https://example.org/x": {"ratio": 1.5}})
    pointer = _bound(scene.formation.project_root, "bad.json", bad)
    refused = inbox(scene, "impl-2", "report", message_id, next_read=pointer, handoff={"outcome": "escalated"})
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_invalid")
    assert _kinds(scene, original["handoff_id"])[-1] == "accepted"
