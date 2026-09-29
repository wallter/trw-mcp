"""MCP-MIDDLEWARE-STORE-OPEN: a broken memory store never fails a tool that is not about memory.

The 8.0.0 incident: a stale server (booted from 7.0.1 code, schema 12, over a schema-13
store) failed unrelated tools such as ``trw_dispatch``. On 8.0.1 no tool-call middleware
opens the store. A tool may still read it for a learning nudge (``trw_checkpoint``'s
ceremony status), and that read degrades to no nudge, never to a failed call. Every path to
the store goes through ``selected_store`` / ``daemon_store_for`` (both imported at call
time), so a store that refuses there stands in for any stale, mismatched or unreachable one.
The nudge pool is pinned to learnings so the nudge read happens on every call, not by
weighted chance. The version-drift advisory (``middleware/version_drift.py``) carries the
/mcp remedy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from trw_memory.exceptions import DaemonVersionMismatchError

from trw_mcp.models.config import get_config
from trw_mcp.server._app import create_app
from trw_mcp.server._tools import _tool_registrars

#: fastmcp's Client is generic over its transport; these tests only call tools on it.
Connected = Client[Any]

_STALE = "stale store: schema 13 is newer than this server's 12"


@pytest.fixture
def opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every store open, each refused the way a stale server's store is."""
    calls: list[str] = []

    def refuse(*_args: object, **_kwargs: object) -> object:
        calls.append("open")
        raise DaemonVersionMismatchError(_STALE)

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(
        "project_namespace: project:storeopen-aaaaaaaa\n"
        "nudge_pool_weights: {workflow: 0, learnings: 100, ceremony: 0, context: 0}\n"
        "dispatch_tools_exposed: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("trw_mcp.server._boot_deferred._resolve_backend_sync", lambda: None)
    monkeypatch.setattr(get_config(), "embeddings_enabled", False)
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", refuse)
    monkeypatch.setattr("trw_mcp.state._daemon_store.daemon_store_for", refuse)
    return calls


@pytest.fixture
async def client(opened: list[str]) -> AsyncIterator[Connected]:
    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as connected:
        await connected.call_tool("trw_session_start", {}, raise_on_error=False)
        await connected.call_tool("trw_init", {"task_name": "store-open"}, raise_on_error=False)
        opened.clear()
        yield connected


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("trw_dispatch", {"action": "status", "target": "none"}),
        ("trw_status", {}),
        ("trw_checkpoint", {"message": "milestone"}),
        ("trw_inbox", {"action": "status"}),
    ],
)
async def test_a_tool_that_is_not_about_memory_does_not_fail_on_a_stale_store(
    client: Connected,
    opened: list[str],
    tool: str,
    arguments: dict[str, object],
) -> None:
    result = await client.call_tool(tool, arguments, raise_on_error=False)

    assert not result.is_error, result.content
    assert "tool_not_in_surface" not in str(result.structured_content), "the tool never ran: the surface denied it"
    assert _STALE not in str(result.content), result.content


async def test_control_a_memory_tool_does_reach_the_refusing_store(client: Connected, opened: list[str]) -> None:
    """Without this, the tests above would pass against a hook nothing calls."""
    await client.call_tool("trw_recall", {"query": "token refresh"}, raise_on_error=False)

    assert opened, "trw_recall never opened the store: the refusal hook is not on the store path"


async def test_control_the_checkpoint_nudge_read_reaches_the_refusing_store_and_degrades(
    client: Connected,
    opened: list[str],
) -> None:
    """The worst case above is real: the checkpoint's learning nudge did read the broken store."""
    result = await client.call_tool("trw_checkpoint", {"message": "milestone"}, raise_on_error=False)

    assert opened, "the pinned learnings pool never read the store, so the checkpoint case proves nothing"
    assert not result.is_error, result.content
