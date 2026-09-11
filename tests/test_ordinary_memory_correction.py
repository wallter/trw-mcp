"""CORE218 CA1-3: ordinary correction needs no dynamic tool-unlock round trip."""

import pytest
from fastmcp import Client

from trw_mcp.models.config import get_config
from trw_mcp.server._app import create_app
from trw_mcp.server._tools import _tool_registrars


@pytest.mark.parametrize("task_type", [None, "coding", "research", "docs", "eval", "rca", "planning", "unknown"])
async def test_fresh_catalog_can_correct_and_retire_memory(tmp_project, monkeypatch, task_type):
    monkeypatch.setenv("TRW_TOOL_RESOLUTION_MODE", "standard")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr("trw_mcp.middleware.surface_authority.resolve_task_type", lambda **kwargs: task_type)
    monkeypatch.setattr(get_config(), "embeddings_enabled", False)
    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as client:
        initial = [tool.name for tool in await client.list_tools()]
        assert initial.count("trw_learn_update") == 1
        recorded = await client.call_tool(
            "trw_learn",
            {
                "summary": "Retryprobe reconnect requires a new session",
                "detail": "Observed retryprobe transport behavior; preserve request identity across retries.",
                "tags": ["retryprobe"],
                "scope": "project",
            },
        )
        payload = recorded.structured_content
        assert payload["status"] == "recorded", payload
        identity = payload["learning_id"]
        changed = await client.call_tool(
            "trw_learn_update",
            {
                "learning_id": identity,
                "summary": "Retryprobe reconnect preserves the existing session",
            },
        )
        assert not changed.is_error
        recalled = await client.call_tool(
            "trw_recall",
            {
                "query": "retryprobe",
                "compact": False,
                "include_tiers": ["project"],
            },
        )
        rows = recalled.structured_content["learnings"]
        row = next(row for row in rows if row["id"] == identity)
        assert row["summary"] == "Retryprobe reconnect preserves the existing session"
        assert row["detail"] == "Observed retryprobe transport behavior; preserve request identity across retries."
        retired = await client.call_tool("trw_learn_update", {"learning_id": identity, "status": "obsolete"})
        assert not retired.is_error
        after = await client.call_tool(
            "trw_recall",
            {
                "query": "retryprobe",
                "compact": False,
                "include_tiers": ["project"],
            },
        )
        # Production output shaping omits empty lists; the explicit counts must
        # still prove an empty active result, not a missing/error response.
        assert not after.is_error
        assert after.structured_content["total_matches"] == 0
        assert after.structured_content["total_available"] == 0


@pytest.mark.parametrize("mode", ["standard", "all"])
async def test_reviewer_cannot_call_ordinary_memory_writes(tmp_project, monkeypatch, mode):
    monkeypatch.setenv("TRW_TOOL_RESOLUTION_MODE", mode)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as client:
        initial = {tool.name for tool in await client.list_tools()}
        assert "trw_recall" in initial
        assert not initial.intersection({"trw_learn", "trw_learn_update"})
        for name, arguments in [
            ("trw_learn", {"summary": "Unauthorized entry", "detail": "Must never be written"}),
            ("trw_learn_update", {"learning_id": "L-protected", "summary": "Must never replace"}),
        ]:
            denied = await client.call_tool(name, arguments)
            assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"
