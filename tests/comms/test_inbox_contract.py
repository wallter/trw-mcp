"""Independent initialized-client request/reply and ACK contract (not native wake)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms import test_send_contract as contract
from tests.comms.test_policy import SendScene as PolicyScene
from tests.comms.test_policy import scene as policy_scene  # noqa: F401
from trw_mcp.comms._envelope import canonical_bytes
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
    scene.server.add_middleware(ResponseOptimizerMiddleware())


async def test_initialized_clients_exchange_request_reply_and_explicit_ack(send_scene: SendScene) -> None:
    add_real_response_middleware(send_scene)
    async with Client(send_scene.server) as sender, Client(send_scene.server) as receiver:
        assert (await call(sender, send_scene, "sender", "trw_inbox", action="enroll"))["status"] == "ok"
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
        assert "next_cursor" not in fetched
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


# --- PRD-CORE-274 Amendment 01 (FR11): wait_seconds transport boundary --------


def _refusal_rows(scene: SendScene) -> list[tuple[Any, ...]]:
    import sqlite3

    from trw_mcp.comms._store import database_path

    conn = sqlite3.connect(database_path(scene.owner / "formation.yaml"))
    try:
        rows: list[tuple[Any, ...]] = conn.execute("SELECT reason, count FROM refusal_counts").fetchall()
        return rows
    finally:
        conn.close()


@pytest.mark.parametrize("wait_seconds", [True, 1.0, 1.5, "1"], ids=["bool", "float", "non_integer_float", "str"])
async def test_non_integer_wait_seconds_rejected_at_the_transport_before_the_handler_runs(
    send_scene: SendScene, wait_seconds: Any
) -> None:
    """Strict-int schema (FR11 item 3): the handler must not be entered at all."""
    add_real_response_middleware(send_scene)
    before = _refusal_rows(send_scene)
    async with Client(send_scene.server) as client:
        send_scene.monkeypatch.setenv("TRW_SESSION_ID", "send-contract-receiver")
        result = await client.call_tool(
            "trw_inbox", {"action": "fetch", "wait_seconds": wait_seconds}, raise_on_error=False
        )
    assert result.is_error, f"{wait_seconds!r} must be refused at the schema boundary, not reach the handler"
    from mcp.types import TextContent

    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
    assert "int_type" in text or "valid integer" in text.lower()
    assert _refusal_rows(send_scene) == before, "a schema rejection must record no closure/counter change"


@pytest.mark.parametrize("wait_seconds", [-1, 31, 10**1000], ids=["negative", "over_cap", "oversized"])
async def test_out_of_range_wait_seconds_reach_the_handler_and_refuse_after_closure(
    send_scene: SendScene, wait_seconds: int
) -> None:
    """Values the schema accepts as a real Python ``int`` reach the handler and refuse there."""
    add_real_response_middleware(send_scene)
    async with Client(send_scene.server) as client:
        send_scene.monkeypatch.setenv("TRW_SESSION_ID", "send-contract-receiver")
        result = await client.call_tool(
            "trw_inbox", {"action": "fetch", "wait_seconds": wait_seconds}, raise_on_error=False
        )
    assert not result.is_error, "an in-range-typed but out-of-bound int must reach the handler, not the transport"
    assert result.structured_content is not None
    assert result.structured_content["status"] == "refused"
    assert result.structured_content["reason"] == "invalid_wait_seconds"
    assert any(reason == "invalid_inbox_arguments" for reason, _count in _refusal_rows(send_scene))


@pytest.fixture
def wait_scene(policy_scene: PolicyScene) -> PolicyScene:
    policy_scene.server.add_middleware(ResponseOptimizerMiddleware())
    return policy_scene


async def _wait_invoke(client: Client[Any], **args: Any) -> dict[str, Any]:
    from mcp.types import TextContent

    result = await client.call_tool("trw_inbox", args)
    payload = result.structured_content
    assert isinstance(payload, dict)
    first_block = result.content[0]
    assert isinstance(first_block, TextContent)
    assert json.loads(first_block.text) == payload
    return payload


async def _send_as(wait_scene: PolicyScene, member: str, key: str, body: str) -> dict[str, Any]:
    wait_scene.actor(member)
    async with Client(wait_scene.server) as client:
        result = await client.call_tool("trw_send", {"recipient_member_id": "impl-2", "request_key": key, "body": body})
    payload = result.structured_content
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(
    "policy_scene", [{"comms_body_max_bytes": 64, "comms_response_max_bytes": 6 * 64 + 4096}], indirect=True
)
async def test_a_waited_page_carries_a_maximally_escaped_body_intact(wait_scene: PolicyScene) -> None:
    body = ('"\\' + "\n\t") * 16  # every character in this body requires JSON escaping
    assert len(body.encode("utf-8")) <= 64
    sent = await _send_as(wait_scene, "impl-1", "escaped", body)
    assert sent["status"] == "ok"
    wait_scene.actor("impl-2")
    async with Client(wait_scene.server) as client:
        fetched = await _wait_invoke(client, action="fetch", wait_seconds=1)
    assert fetched["items"][0]["body"] == body
    assert len(canonical_bytes(fetched)) <= wait_scene.config.comms_response_max_bytes


@pytest.mark.parametrize("policy_scene", [{"comms_fetch_max_items": 1}], indirect=True)
async def test_page_limit_continuation_holds_through_a_waited_page(wait_scene: PolicyScene) -> None:
    first_receipt = (await _send_as(wait_scene, "impl-1", "one", "one"))["receipt"]
    second_receipt = (await _send_as(wait_scene, "impl-1", "two", "two"))["receipt"]
    wait_scene.actor("impl-2")
    async with Client(wait_scene.server) as client:
        first = await _wait_invoke(client, action="fetch", wait_seconds=1)
        assert [item["message_id"] for item in first["items"]] == [first_receipt["message_id"]]
        assert first["next_cursor"] is not None
        # A positive wait_seconds requires action=fetch with NO cursor
        # (wait_requires_fresh_fetch); the continuation page is an ordinary,
        # zero-wait fetch, exactly as it is for a non-waited first page.
        second = await _wait_invoke(client, action="fetch", cursor=first["next_cursor"])
        assert [item["message_id"] for item in second["items"]] == [second_receipt["message_id"]]
        assert "next_cursor" not in second


async def test_a_lost_waited_response_replays_the_same_pending_items_on_the_next_fetch(wait_scene: PolicyScene) -> None:
    receipt = (await _send_as(wait_scene, "impl-1", "once", "payload"))["receipt"]
    wait_scene.actor("impl-2")
    async with Client(wait_scene.server) as client:
        first = await _wait_invoke(client, action="fetch", wait_seconds=1)
        # The caller never sees/acts on `first` (simulating a lost response); a
        # fresh call must recover the SAME still-pending item, not skip it.
        second = await _wait_invoke(client, action="fetch", wait_seconds=1)
    assert first["items"] == second["items"] == [{**receipt, "body": "payload"}]
