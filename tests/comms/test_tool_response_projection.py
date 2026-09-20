"""Compact tool envelopes without changing internal facades or peer content."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import FastMCP
from mcp.types import TextContent

from trw_mcp.tools import swarm_comms


@pytest.mark.parametrize(
    "tool,args,facade",
    [
        ("trw_peers", {}, "peers"),
        ("trw_send", {"request_key": "r", "body": "b", "recipient_member_id": "peer"}, "send"),
        ("trw_inbox", {}, "inbox"),
    ],
)
@pytest.mark.parametrize("cursor", [None, "opaque-cursor"])
async def test_only_redundant_top_level_fields_are_omitted(
    monkeypatch: pytest.MonkeyPatch, tool: str, args: dict[str, Any], facade: str, cursor: str | None
) -> None:
    original = {
        "status": "ok",
        "delivery": "pull_only",
        "items": [],
        "next_cursor": cursor,
        "receipt": {"time": 1.123456789, "delivery": "pull_only", "next_cursor": None},
        "body": '{"delivery":"pull_only","items":[]}',
        "count": 0,
        "enabled": False,
    }
    monkeypatch.setattr(swarm_comms, facade, lambda *a, **kw: original)
    server = FastMCP("compact-envelope")
    swarm_comms.register_swarm_comms_tools(server)
    result = await server.call_tool(tool, args)
    expected = {k: v for k, v in original.items() if k != "delivery" and not (k == "next_cursor" and v is None)}
    assert result.structured_content == expected
    assert json.loads(next(b.text for b in result.content if isinstance(b, TextContent))) == expected
    assert original["delivery"] == "pull_only" and "next_cursor" in original


async def test_refusal_reason_and_remedy_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        swarm_comms,
        "inbox",
        lambda *a, **kw: {
            "status": "refused",
            "reason": "not_joined",
            "detail": "Join the declared member first.",
            "delivery": "pull_only",
        },
    )
    server = FastMCP("refusal")
    swarm_comms.register_swarm_comms_tools(server)
    result = await server.call_tool("trw_inbox", {})
    assert result.structured_content == {
        "status": "refused",
        "reason": "not_joined",
        "detail": "Join the declared member first.",
    }


def test_unrecognized_transport_label_is_not_silently_removed() -> None:
    payload = {"status": "ok", "delivery": "future_transport", "next_cursor": "opaque"}
    assert swarm_comms._tool_response(payload) == payload
