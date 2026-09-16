"""Independent initialized-client request/reply and ACK contract (not native wake)."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Client

from tests.comms import test_send_contract as contract
from trw_mcp.middleware.context_budget import ContextBudgetMiddleware
from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

SendScene = contract.SendScene
send_scene = contract.send_scene


async def call(client: Any, scene: SendScene, member: str, name: str, **arguments: Any) -> dict[str, Any]:
    scene.monkeypatch.setenv("TRW_SESSION_ID", "send-contract-" + member)
    result = await client.call_tool(name, arguments, raise_on_error=False)
    assert not result.is_error
    payload = result.structured_content
    assert isinstance(payload, dict)
    assert json.loads(result.content[0].text) == payload, "text-only consumer received a different payload"
    return payload


def add_real_response_middleware(scene: SendScene) -> None:
    scene.server.add_middleware(ContextBudgetMiddleware())
    scene.server.add_middleware(ResponseOptimizerMiddleware())


async def test_initialized_clients_exchange_request_reply_and_explicit_ack(send_scene: SendScene) -> None:
    add_real_response_middleware(send_scene)
    async with Client(send_scene.server) as sender, Client(send_scene.server) as receiver:
        assert (await call(sender, send_scene, "sender", "trw_peers", action="enroll"))["status"] == "ok"
        sent = await call(
            sender,
            send_scene,
            "sender",
            "trw_send",
            recipient_member_id="receiver",
            request_key="request",
            body="done",
            delivery_class="interrupt",
        )
        assert sent["status"] == "ok"
        # Discard the first result; preparation is not evidence of client receipt.
        await call(receiver, send_scene, "receiver", "trw_inbox", action="fetch")
        fetched = await call(receiver, send_scene, "receiver", "trw_inbox", action="fetch")
        assert fetched["status"] == "ok"
        assert fetched["next_cursor"] is None
        assert len(fetched["items"]) == 1
        item = fetched["items"][0]
        assert item == {**sent["receipt"], "body": "done"}
        message_id = item["message_id"]
        ack = await call(receiver, send_scene, "receiver", "trw_inbox", action="ack", message_ids=[message_id])
        assert ack["acknowledged_ids"] == [message_id]
        assert await call(receiver, send_scene, "receiver", "trw_inbox", action="ack", message_ids=[message_id]) == ack
        assert (await call(receiver, send_scene, "receiver", "trw_inbox", action="fetch"))["items"] == []
        reply = await call(
            receiver,
            send_scene,
            "receiver",
            "trw_send",
            recipient_member_id="sender",
            request_key="reply",
            body="reply",
            kind="reply",
            delivery_class="on_idle",
        )
        assert reply["status"] == "ok"
        response = await call(sender, send_scene, "sender", "trw_inbox", action="fetch")
        assert response["items"] == [{**reply["receipt"], "body": "reply"}]
        await call(
            sender, send_scene, "sender", "trw_inbox", action="ack", message_ids=[reply["receipt"]["message_id"]]
        )
        status = await call(sender, send_scene, "sender", "trw_inbox", action="status")
        assert {item["message_id"] for item in status["items"]} == {message_id, reply["receipt"]["message_id"]}
        assert all(item["state"] == "acked" and "body" not in item for item in status["items"])
        assert all(set(item["milestones"]) == {"admitted", "fetch_prepared", "acked"} for item in status["items"])
        from trw_mcp import formation

        loaded = formation.load(send_scene.owner, trw_dir=send_scene.root / ".trw")
        assert loaded is not None
        assert {member.status for member in loaded.manifest.members} == {"joined"}, "message text became completion"


async def test_mixed_ack_is_atomic_and_count_limit_precedes_duplicate_normalization(send_scene: SendScene) -> None:
    add_real_response_middleware(send_scene)
    async with Client(send_scene.server) as sender:
        accepted = await call(
            sender,
            send_scene,
            "sender",
            "trw_send",
            recipient_member_id="receiver",
            request_key="ack-bound",
            body="test",
        )
    assert accepted["status"] == "ok"
    message_id = accepted["receipt"]["message_id"]
    async with Client(send_scene.server) as receiver:
        refused = await call(
            receiver, send_scene, "receiver", "trw_inbox", action="ack", message_ids=[message_id, "f" * 32]
        )
        assert refused["status"] == "refused"
        status = await call(receiver, send_scene, "receiver", "trw_inbox", action="status")
        assert status["items"][0]["state"] == "pending"
        assert set(status["items"][0]["milestones"]) == {"admitted"}
        send_scene.config.comms_fetch_max_items = 1
        duplicate_overflow = await call(
            receiver, send_scene, "receiver", "trw_inbox", action="ack", message_ids=[message_id, message_id]
        )
        assert duplicate_overflow["status"] == "refused"
        # A current authorized ID may be ACKed without prior fetch.
        valid = await call(receiver, send_scene, "receiver", "trw_inbox", action="ack", message_ids=[message_id])
        assert valid["acknowledged_ids"] == [message_id]
