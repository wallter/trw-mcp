"""CORE218 CA1-3: ordinary correction needs no dynamic tool-unlock round trip."""

from pathlib import Path

import pytest
from fastmcp import Client

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.models.config import get_config, reload_config
from trw_mcp.server._app import create_app
from trw_mcp.server._tools import _tool_registrars


@pytest.mark.parametrize("task_type", [None, "coding", "research", "docs", "eval", "rca", "planning", "unknown"])
async def test_fresh_catalog_can_correct_and_retire_memory(
    memory_daemon: MemoryDaemon, tmp_path: Path, monkeypatch, task_type, request: pytest.FixtureRequest
):
    monkeypatch.setenv("TRW_TOOL_RESOLUTION_MODE", "standard")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr("trw_mcp.middleware.surface_authority.resolve_task_type", lambda **kwargs: task_type)
    # The server/tool path resolves ``trw_dir`` through the test suite's own
    # path-isolation stand-in (``tests/_path_isolation.py``), which always
    # answers ``<the test's tmp_path>/.trw`` regardless of TRW_PROJECT_ROOT —
    # so this checkout is built there directly (the pattern _memory_fixtures.py
    # documents for "a test that builds its own .trw elsewhere"), rather than
    # via the ``daemon_checkout`` fixture's own ``tmp_path/repo/.trw`` layout.
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))

    def _no_autostart(_paths: object) -> None:
        raise AssertionError("a test tried to start a second memory daemon")

    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    trw_dir = tmp_path / ".trw"
    attach_checkout(trw_dir, memory_daemon)
    reload_config()
    request.addfinalizer(reload_config)
    monkeypatch.setattr(get_config(), "embeddings_enabled", False)
    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as client:
        initial = [tool.name for tool in await client.list_tools()]
        assert initial.count("trw_learn") == 1
        assert initial.count("trw_learn_update") == 0
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
            "trw_learn",
            {
                "learning_id": identity,
                "summary": "Retryprobe reconnect preserves the existing session",
            },
        )
        assert not changed.is_error
        recalled = await client.call_tool("trw_recall", {"ids": [identity]})
        rows = recalled.structured_content["learnings"]
        row = next(row for row in rows if row["id"] == identity)
        assert row["summary"] == "Retryprobe reconnect preserves the existing session"
        assert row["detail"] == "Observed retryprobe transport behavior; preserve request identity across retries."
        retired = await client.call_tool("trw_learn", {"learning_id": identity, "status": "obsolete"})
        assert not retired.is_error
        after = await client.call_tool(
            "trw_recall",
            {
                "query": "retryprobe",
                "options": {"include_tiers": ["project"]},
            },
        )
        # Production output shaping omits empty lists; the explicit counts must
        # still prove an empty active result, not a missing/error response.
        assert not after.is_error
        assert after.structured_content["total_matches"] == 0


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
        assert "trw_learn_update" not in initial  # merged into trw_learn by PRD-CORE-291
        assert not initial.intersection({"trw_learn"})
        for name, arguments in [
            ("trw_learn", {"summary": "Unauthorized entry", "detail": "Must never be written"}),
            ("trw_learn", {"learning_id": "L-protected", "summary": "Must never replace"}),
        ]:
            denied = await client.call_tool(name, arguments)
            assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"
