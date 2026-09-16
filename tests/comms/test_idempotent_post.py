"""CORE274 lifetime receipt semantics and immutable exact retry precedence."""

from __future__ import annotations

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401


def test_sender_needs_membership_not_enrollment_and_retry_survives_restart(scene: SendScene) -> None:
    from trw_mcp.comms import _endpoints

    assert scene.rows("SELECT member_id FROM endpoints") == [("impl-2",)]
    first = scene.send()
    assert first["status"] == "ok"
    _endpoints._reset_process_incarnations_for_test()
    assert scene.send() == first
    assert scene.rows("SELECT charge FROM groups") == [(1,)]
    assert scene.rows("SELECT fact FROM milestones") == [("admitted",)]
    assert set(first["receipt"]) == {
        "message_id",
        "sender_member_id",
        "recipient_member_id",
        "kind",
        "delivery_class",
        "admitted_at",
    }


@pytest.mark.parametrize(
    "changed",
    [{"body": "changed"}, {"recipient_member_id": "impl-1"}, {"kind": "status"}, {"delivery_class": "interrupt"}],
)
def test_every_changed_canonical_field_conflicts(scene: SendScene, changed: dict[str, str]) -> None:
    assert scene.send()["status"] == "ok"
    assert scene.send(**changed)["reason"] == "idempotency_conflict"
    assert scene.rows("SELECT charge FROM groups") == [(1,)]


@pytest.mark.parametrize("scene", [{"comms_group_admission_limit": 1}], indirect=True)
def test_exact_retry_bypasses_exhausted_budget_and_expired_recipient(scene: SendScene) -> None:
    first = scene.send()
    assert first["status"] == "ok"
    scene.rows("UPDATE groups SET group_time=group_time+1000")
    assert scene.send() == first
    assert scene.send("new")["reason"] == "recipient_unavailable"
    assert scene.rows("SELECT charge FROM groups") == [(1,)]
