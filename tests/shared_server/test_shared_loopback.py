"""The one real-network test: a real proxy auto-starts a real shared server on loopback, then survives a swap.

Also pins the identity claim the design rests on: in stateless mode FastMCP's
``Context.session_id`` is the ``Mcp-Session-Id`` header the proxy sends (checked
in-process through an ASGI transport, so that half needs no socket).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Context, FastMCP

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _ops
from trw_mcp.shared_server._records import SharedPaths, read_live_record, read_token
from trw_mcp.shared_server._server import Door

pytestmark = [pytest.mark.integration, pytest.mark.slow]


async def test_context_session_id_is_the_proxys_header_in_stateless_mode() -> None:
    server = FastMCP("probe")

    @server.tool
    def whoami(ctx: Context) -> str:
        return ctx.session_id

    app = server.http_app(transport="streamable-http", stateless_http=True, json_response=True)
    door = Door(app, token="tok", env="stable", version="x", max_inflight=8)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=door), base_url="http://127.0.0.1") as client,
    ):
        seen = []
        for identity in ("client-a", "client-b"):
            answer = await client.post(
                "/mcp",
                json=call,
                headers={
                    "authorization": "Bearer tok",
                    "mcp-session-id": identity,
                    "accept": "application/json, text/event-stream",
                },
            )
            seen.append(answer.json()["result"]["structuredContent"]["result"])
    assert seen == ["client-a", "client-b"]


def _pid_gone(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    return False


@pytest.mark.timeout(300)
@pytest.mark.duration_exempt("boots two real trw-mcp servers (auto-start, then a swap successor)")
def test_a_real_proxy_starts_the_server_and_survives_a_hot_swap(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("shared_mcp:\n  enabled: true\n", encoding="utf-8")
    paths = SharedPaths.resolve(project / ".trw", SharedMcpConfig(enabled=True))
    proxy = subprocess.Popen(
        [sys.executable, "-m", "trw_mcp.shared_server"],
        cwd=project,
        env={**os.environ, "TRW_SESSION_ID": "client-a"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proxy.stdin is not None and proxy.stdout is not None
    started: list[int] = []

    def ask(message: dict[str, Any]) -> dict[str, Any]:
        proxy.stdin.write(json.dumps(message).encode() + b"\n")  # type: ignore[union-attr]
        proxy.stdin.flush()  # type: ignore[union-attr]
        return dict(json.loads(proxy.stdout.readline()))  # type: ignore[union-attr]

    try:
        init = ask(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "loopback", "version": "0"},
                },
            }
        )
        assert init["result"]["serverInfo"]["name"]
        first = read_live_record(paths, "stable")
        assert first is not None
        started.append(first.pid)
        tools = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert any(tool["name"] == "trw_session_start" for tool in tools["result"]["tools"])

        report = _ops.swap(paths, "stable", Path(sys.executable), project_root=project, expect=None)
        second = read_live_record(paths, "stable")
        assert second is not None and second.pid != first.pid, report
        started.append(second.pid)

        again = ask({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert again["result"]["tools"], "the same proxy reaches the successor without reconnecting the client"
        assert _pid_gone(first.pid, 60), "the drained predecessor exits"
    finally:
        proxy.stdin.close()
        proxy.wait(timeout=30)
        live = read_live_record(paths, "stable")
        if live is not None:  # drain what this test started, so nothing outlives it
            httpx.post(
                live.url.removesuffix("/mcp") + "/admin/drain",
                headers={"authorization": f"Bearer {read_token(paths)}"},
                timeout=5,
            )
        for pid in started:
            _pid_gone(pid, 30)
