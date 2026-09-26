"""Transport preservation through initialized SDK sessions, not T3 implementation.

The three named producers are synthetic bounded envelopes. These tests exercise
real response middleware and MCP serialization, not inbox storage or authority.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

COMMS_NAMES = ("trw_send", "trw_inbox")


def envelope(index: int, *, empty: bool = False) -> dict[str, Any]:
    return {
        "status": "ok",
        "delivery": "pull_only",
        "summary": f"summary-{index}:" + "x" * 1000,
        "items": [] if empty else [{"body": f"body-{index}:" + '🙂\\"\n' * 250, "admitted_at": 123.456789}],
        "next_cursor": None if empty or index % 2 == 0 else f"v1.cursor-{index}",
        "empty_metadata": {},
        "lease_expires_at": 987.654321,
    }


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def server_for(name: str) -> tuple[FastMCP, list[int]]:
    server = FastMCP("synthetic-lossless-transport")
    server.add_middleware(ResponseOptimizerMiddleware())
    calls: list[int] = []

    @server.tool(name=name)
    def bounded_producer(index: int = 0, empty: bool = False) -> ToolResult:
        calls.append(index)
        payload = envelope(index, empty=empty)
        return ToolResult(content=[TextContent(type="text", text=canonical(payload))], structured_content=payload)

    return server, calls


def assert_exact(result: Any, expected: dict[str, Any]) -> None:
    assert result.structured_content == expected
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == canonical(expected)
    assert json.loads(result.content[0].text) == expected


@pytest.mark.parametrize("name", COMMS_NAMES)
@pytest.mark.parametrize("response_format", ["json", "yaml"])
async def test_discarded_response_retry_and_long_session_are_lossless(
    name: str, response_format: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_RESPONSE_FORMAT", response_format)
    server, calls = server_for(name)
    async with Client(server) as client:
        await client.call_tool(name, {"index": 0})  # caller discards first response; no ACK implied
        assert_exact(await client.call_tool(name, {"index": 0}), envelope(0))
        for index in range(1, 33):  # a long session
            assert_exact(await client.call_tool(name, {"index": index}), envelope(index))
        assert_exact(await client.call_tool(name, {"empty": True}), envelope(0, empty=True))
    assert len(calls) == 35  # bypass is output-only; every producer still executes


async def test_other_tools_still_optimize_and_a_repeat_arrives_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_RESPONSE_FORMAT", "json")
    # A similar prefix is deliberately not a member of the exact exception set.
    name = "trw_inbox_preview"
    server, calls = server_for(name)
    async with Client(server) as client:
        first = await client.call_tool(name, {"index": 0})
        assert first.structured_content["items"][0]["admitted_at"] == 123.46
        assert "next_cursor" not in first.structured_content
        repeated = await client.call_tool(name, {"index": 0})
        # Nothing downstream elides a repeat: the server cannot know the client still holds the first.
        assert repeated.content[0].text == first.content[0].text
        for index in range(1, 33):
            result = await client.call_tool(name, {"index": index})
            # No call-count-dependent cut: a late response reads exactly what the tool returned.
            assert json.loads(result.content[0].text)["summary"] == result.structured_content["summary"]
    assert len(calls) == 34


@pytest.mark.parametrize("name", COMMS_NAMES)
async def test_lossless_exception_does_not_bypass_inner_denial(name: str) -> None:
    class Deny(Middleware):
        async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
            raise ToolError("denied by downstream authority")

    server, calls = server_for(name)
    server.add_middleware(Deny())
    async with Client(server) as client:
        with pytest.raises(ToolError, match="denied by downstream authority"):
            await client.call_tool(name)
    assert calls == []


async def test_process_local_optimizer_exception_disabled_restores_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_RESPONSE_FORMAT", "json")
    monkeypatch.setattr("trw_mcp.middleware.response_optimizer.LOSSLESS_COMMS_TOOLS", frozenset())
    server, calls = server_for("trw_inbox")
    async with Client(server) as client:
        for _ in range(2):
            result = await client.call_tool("trw_inbox")
            assert result.structured_content["items"][0]["admitted_at"] == 123.46
            assert "next_cursor" not in result.structured_content
            assert "empty_metadata" not in result.structured_content
            assert json.loads(result.content[0].text) == result.structured_content
    assert calls == [0, 0]
