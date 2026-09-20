"""CORE274 initialized transport: all delivery classes remain explicit pull."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.messages import MessageHandler

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401


@pytest.mark.parametrize("delivery_class", ["on_demand", "interrupt", "on_idle"])
async def test_all_classes_no_notifications_or_unrelated_response_injection(
    transport_scene: SendScene, delivery_class: str
) -> None:
    s = transport_scene
    events: list[Any] = []

    class Capture(MessageHandler):
        async def on_message(self, message: Any) -> None:
            events.append(message)

    @s.server.tool()
    def unrelated_probe() -> dict[str, str]:
        return {"ordinary": "unchanged"}

    # This is an actual initialized MCP client with a server-message handler.
    async with Client(s.server, message_handler=Capture()) as client:
        events.clear()  # initialization itself is outside the delivery claim
        sent = await invoke(
            client,
            "trw_send",
            recipient_member_id="impl-2",
            request_key=delivery_class,
            body="untrusted-peer-body",
            delivery_class=delivery_class,
        )
        if delivery_class == "on_demand":
            assert "delivery" not in sent
        else:
            assert sent["delivery"] == "pull_only"
        s.actor("impl-2")
        ordinary = await client.call_tool("unrelated_probe")
        assert ordinary.structured_content == {"ordinary": "unchanged"}
        assert "untrusted-peer-body" not in ordinary.content[0].text
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(0,)]
        fetched = await invoke(client, "trw_inbox")
        assert "delivery" not in fetched
        assert fetched["items"][0]["body"] == "untrusted-peer-body"
        assert fetched["items"][0]["delivery_class"] == delivery_class
        assert events == []


@pytest.mark.parametrize("disable_ack_scope", [False, True])
async def test_ack_recipient_guard_and_process_local_negative_control(
    transport_scene: SendScene, disable_ack_scope: bool
) -> None:
    from trw_mcp.comms import _inbox_page

    s = transport_scene
    async with Client(s.server) as client:
        assert (await invoke(client, "trw_peers", action="enroll"))["status"] == "ok"
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="q", body="for-two")
        message_id = sent["receipt"]["message_id"]
        if disable_ack_scope:

            def unguarded(conn: Any, binding: Any, ids: Any) -> Any:
                return [conn.execute("SELECT * FROM admissions WHERE message_id=?", (item,)).fetchone() for item in ids]

            s.monkeypatch.setattr(_inbox_page, "validate_ack", unguarded)
        result = await invoke(client, "trw_inbox", action="ack", message_ids=[message_id])
        if disable_ack_scope:
            assert result["status"] == "ok"
            assert s.rows("SELECT state FROM admissions") == [("acked",)]
        else:
            assert result["reason"] == "ack_not_authorized"
            assert s.rows("SELECT state FROM admissions") == [("pending",)]
