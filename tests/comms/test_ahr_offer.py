"""PRD-CORE-349 FR02, FR06, NFR02: offering a sealed AHR through trw_send, and AHR expiry.

Everything runs through the real ``trw_send``/``trw_inbox`` tool dispatch. Each refusal names its
closed reason and appends no event.
"""

from __future__ import annotations

import time

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms._ahr_support import call, events, handoff_doc, inbox, offer, read_back, readback_doc, write
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.handoff import digest


def _no_events(s: SendScene) -> bool:
    return s.rows("SELECT COUNT(*) FROM ahr_events") == [(0,)]


def test_offer_stores_the_record_sets_the_pointer_body_and_appends_offered(scene: SendScene) -> None:
    handoff = handoff_doc()
    sent = offer(scene, handoff)
    assert sent["status"] == "ok", sent
    message_id = sent["receipt"]["message_id"]
    ((body,),) = scene.rows("SELECT body FROM admissions WHERE message_id=?", (message_id,))
    assert body == f"ahr:1 {handoff['handoff_id']} {digest(handoff)}"
    assert events(scene, handoff["handoff_id"]) == [(1, "offered", "impl-1")]
    ((record, subject),) = scene.rows("SELECT record,subject FROM ahr_events WHERE seq=1")
    assert subject == handoff["subject"] and b'"handoff_id"' in bytes(record)


def test_an_exact_retry_of_an_offer_appends_nothing(scene: SendScene) -> None:
    handoff = handoff_doc()
    first = offer(scene, handoff)
    again = offer(scene, handoff)
    assert again["status"] == "ok" and again["receipt"]["message_id"] == first["receipt"]["message_id"]
    assert events(scene, handoff["handoff_id"]) == [(1, "offered", "impl-1")]


def test_offer_with_a_body_is_refused_as_a_body_conflict(scene: SendScene) -> None:
    scene.actor("impl-1")
    path = write(scene.formation.project_root, "h.json", handoff_doc())
    refused = call(
        scene, "trw_send", recipient_member_id="impl-2", request_key="k", body="also text", handoff={"path": path}
    )
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_body_conflict")
    assert _no_events(scene)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"tier": "critical"}, "ahr_tier_not_supported"),
        ({"sender": "impl-9"}, "ahr_party_mismatch"),
        ({"receiver": "impl-9"}, "ahr_party_mismatch"),
    ],
)
def test_an_offer_outside_this_store_is_refused(scene: SendScene, changes: dict[str, str], reason: str) -> None:
    refused = offer(scene, handoff_doc(**changes))  # type: ignore[arg-type]
    assert (refused["status"], refused["reason"]) == ("refused", reason)
    assert _no_events(scene) and scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


def test_an_invalid_or_tampered_record_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    handoff["objective"]["goal"] = "Something the digest does not cover."
    refused = offer(scene, handoff)
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_invalid")
    assert _no_events(scene)


def test_an_unaddressed_record_is_refused(scene: SendScene) -> None:
    handoff = handoff_doc()
    handoff = handoff_doc(to={"kind": "unaddressed", "scope": "lane:W1"})
    refused = offer(scene, handoff)
    assert refused["status"] == "refused"
    assert refused["reason"] in ("ahr_unaddressed_not_supported", "ahr_invalid")
    assert _no_events(scene)


@pytest.mark.parametrize("path", ["../outside.json", "/etc/hosts", "handoffs/missing.json"])
def test_a_record_path_that_is_not_a_local_file_is_refused(scene: SendScene, path: str) -> None:
    scene.actor("impl-1")
    refused = call(scene, "trw_send", recipient_member_id="impl-2", request_key="k", body="", handoff={"path": path})
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_ref_not_local")
    assert _no_events(scene)


def test_handoff_on_a_non_request_is_refused(scene: SendScene) -> None:
    scene.actor("impl-1")
    path = write(scene.formation.project_root, "h.json", handoff_doc())
    refused = call(
        scene, "trw_send", recipient_member_id="impl-2", request_key="k", body="", kind="status", handoff={"path": path}
    )
    assert (refused["status"], refused["reason"]) == ("refused", "not_a_handoff")
    assert _no_events(scene)


def test_an_expiry_beyond_the_carrier_ttl_is_refused(scene: SendScene) -> None:
    ttl = scene.config.comms_message_ttl_seconds
    refused = offer(scene, handoff_doc(expires_in=ttl + 3600))
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_expiry_exceeds_ttl")
    assert _no_events(scene)


def test_an_offered_record_past_expires_at_gets_expired_before_any_other_event(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff = handoff_doc(expires_in=60)
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    later = time.time() + 3600
    monkeypatch.setattr("trw_mcp.comms._store.time.time", lambda: later)
    refused = read_back(scene, message_id, readback_doc(handoff))
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert events(scene, handoff["handoff_id"]) == [(1, "offered", "impl-1"), (2, "expired", "trw-comms-store")]
    refused = inbox(scene, "impl-2", "accept", message_id)
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_lifecycle_refused")
    assert len(events(scene, handoff["handoff_id"])) == 2


def test_a_report_ref_outside_the_project_is_refused(scene: SendScene, tmp_path: object) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert read_back(scene, message_id, readback_doc(handoff))["status"] == "ok"
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    refused = inbox(
        scene,
        "impl-2",
        "report",
        message_id,
        next_read="https://example.org/r#sha256:" + "0" * 64,
        handoff={"outcome": "met"},
    )
    assert (refused["status"], refused["reason"]) == ("refused", "ahr_ref_not_local")
    assert [event for _seq, event, _actor in events(scene, handoff["handoff_id"])][-1] == "accepted"


def test_the_receivers_fetch_carries_the_stored_record_as_data(scene: SendScene) -> None:
    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    scene.actor("impl-2")
    (item,) = call(scene, "trw_inbox", action="fetch")["items"]
    assert item["message_id"] == message_id
    assert item["body"].startswith(f"ahr:1 {handoff['handoff_id']} ")
    assert item["ahr_record"] == handoff
    assert digest(item["ahr_record"]) == item["body"].split(" ")[2]


@pytest.mark.parametrize("scene", [{"comms_body_max_bytes": 1024, "comms_response_max_bytes": 10240}], indirect=True)
def test_a_record_larger_than_the_body_limit_is_named_not_inlined(scene: SendScene) -> None:
    handoff = handoff_doc()
    offer(scene, handoff)
    scene.actor("impl-2")
    (item,) = call(scene, "trw_inbox", action="fetch")["items"]
    assert item["ahr_record"]["handoff_id"] == handoff["handoff_id"]
    assert "omitted" in item["ahr_record"] and "objective" not in item["ahr_record"]
