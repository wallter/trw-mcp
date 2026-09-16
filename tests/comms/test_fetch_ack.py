"""CORE274 initialized-client request/reply, bounded fetch and whole-batch ACK."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms._envelope import canonical_bytes
from trw_mcp.middleware.context_budget import ContextBudgetMiddleware
from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware


@pytest.fixture
def transport_scene(scene: SendScene) -> SendScene:
    scene.server.add_middleware(ContextBudgetMiddleware())
    scene.server.add_middleware(ResponseOptimizerMiddleware())
    return scene


async def invoke(client: Client[Any], name: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(name, args)
    payload = result.structured_content
    assert isinstance(payload, dict)
    assert json.loads(result.content[0].text) == payload
    return payload


async def test_native_request_reply_ack_and_sender_status(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        assert (await invoke(client, "trw_peers", action="enroll"))["status"] == "ok"
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="q", body="question")
        request_id = sent["receipt"]["message_id"]
        s.actor("impl-2")
        fetched = await invoke(client, "trw_inbox")
        assert fetched["items"] == [{**sent["receipt"], "body": "question"}]
        ack = await invoke(client, "trw_inbox", action="ack", message_ids=[request_id, request_id])
        assert ack == {"status": "ok", "delivery": "pull_only", "acknowledged_ids": [request_id]}
        assert await invoke(client, "trw_inbox", action="ack", message_ids=[request_id]) == ack
        reply = await invoke(
            client, "trw_send", recipient_member_id="impl-1", request_key="r", body="answer", kind="reply"
        )
        s.actor("impl-1")
        assert (await invoke(client, "trw_inbox"))["items"] == [{**reply["receipt"], "body": "answer"}]
        status = await invoke(client, "trw_inbox", action="status")
        assert len(status["items"]) == 2
        assert all("body" not in item for item in status["items"])
        assert status["items"][0]["state"] == "acked"
        assert set(status["items"][0]["milestones"]) == {"admitted", "fetch_prepared", "acked"}
    assert s.rows("SELECT charge FROM groups") == [(2,)]


async def test_ack_whole_batch_rejects_unknown_before_any_ack(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="q", body="hi")
        message_id = sent["receipt"]["message_id"]
        s.actor("impl-2")
        refused = await invoke(client, "trw_inbox", action="ack", message_ids=[message_id, "0" * 32])
        assert refused["reason"] == "ack_not_authorized"
        assert s.rows("SELECT state FROM admissions") == [("pending",)]
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='acked'") == [(0,)]
        # ACK without any preceding fetch is deliberately allowed.
        assert (await invoke(client, "trw_inbox", action="ack", message_ids=[message_id]))["status"] == "ok"
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(0,)]


@pytest.mark.parametrize("scene", [{"comms_fetch_max_items": 1}], indirect=True)
async def test_count_before_duplicate_normalization_and_append_cursor(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        receipts = []
        for key in ("one", "two", "three"):
            receipts.append(
                (await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=key, body=key))["receipt"]
            )
        s.actor("impl-2")
        first = await invoke(client, "trw_inbox")
        assert first["items"][0]["message_id"] == receipts[0]["message_id"]
        cursor = first["next_cursor"]
        assert cursor
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(1,)]
        refused = await invoke(client, "trw_inbox", action="ack", message_ids=[receipts[0]["message_id"]] * 2)
        assert refused["reason"] == "invalid_ack_ids"
        await invoke(client, "trw_inbox", action="ack", message_ids=[receipts[0]["message_id"]])
        second = await invoke(client, "trw_inbox", cursor=cursor)
        assert second["items"][0]["message_id"] == receipts[1]["message_id"]
        third = await invoke(client, "trw_inbox", cursor=second["next_cursor"])
        assert third["items"][0]["message_id"] == receipts[2]["message_id"]
        assert third["next_cursor"] is None
        fresh = await invoke(client, "trw_inbox")
        assert fresh["items"] == second["items"]  # preparation is not consumption
        assert len(canonical_bytes(fresh)) <= s.config.comms_response_max_bytes


@pytest.mark.parametrize(
    "args",
    [
        {"message_ids": []},
        {"action": "status", "message_ids": []},
        {"action": "ack"},
        {"action": "ack", "message_ids": [], "cursor": "bad"},
        {"cursor": "bad"},
    ],
)
async def test_incompatible_arguments_refuse_without_message_changes(
    transport_scene: SendScene, args: dict[str, Any]
) -> None:
    s = transport_scene
    s.actor("impl-2")
    async with Client(s.server) as client:
        result = await invoke(client, "trw_inbox", **args)
        assert result["status"] == "refused"
        assert s.rows("SELECT COUNT(*) FROM milestones") == [(0,)]


@pytest.mark.parametrize(
    "scene",
    [
        {
            "comms_fetch_max_items": 64,
            "comms_sender_admissions_per_minute": 128,
            "comms_recipient_outstanding_limit": 128,
        }
    ],
    indirect=True,
)
async def test_maximum_item_page_and_ack_fit_live_body_free_bound(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        for index in range(65):
            result = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=str(index), body="x")
            assert result["status"] == "ok"
        s.actor("impl-2")
        first = await invoke(client, "trw_inbox")
        assert len(first["items"]) == 64 and first["next_cursor"] is not None
        second = await invoke(client, "trw_inbox", cursor=first["next_cursor"])
        assert len(second["items"]) == 1 and second["next_cursor"] is None
        ids = [item["message_id"] for item in reversed(first["items"])]
        assert (await invoke(client, "trw_inbox", action="ack", message_ids=[ids[0]] * 65))[
            "reason"
        ] == "invalid_ack_ids"
        s.config.comms_body_max_bytes = 1
        s.config.comms_response_max_bytes = 4102
        ack = await invoke(client, "trw_inbox", action="ack", message_ids=ids)
        assert ack["acknowledged_ids"] == ids
        assert len(canonical_bytes(ack)) <= 4102
        assert (await invoke(client, "trw_inbox"))["reason"] == "response_body_policy_incompatible"
        assert s.rows("SELECT COUNT(*) FROM admissions WHERE state='pending'") == [(1,)]
        assert s.rows("SELECT charge FROM groups") == [(65,)]
