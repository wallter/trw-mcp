"""CORE274: explicit comms opt-in composes with surface and role gates.

PRD-CORE-300 S11b flattened the surface: there is no per-task pack resolution
and no grant ledger any more, so this module no longer parametrizes over
``task_type`` (which no longer affects resolution at all) or plants a grant to
reach a masked reviewer tool — a denial of a directly-called excluded tool is
the retargeted positive control.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import enable_comms, joined_member


@pytest.fixture(autouse=True)
def _reset_surface() -> Iterator[None]:
    from trw_mcp.middleware.surface_authority import reset_surface_authority_state

    reset_surface_authority_state()
    yield
    reset_surface_authority_state()


def test_enabled_resolver_adds_exactly_peer_pack_without_default_growth() -> None:
    from trw_mcp.models.surface_packs import FLAG_GATED_PACKS, KERNEL_TOOLS, PACK_TOOLS
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    default = resolve_tool_surface("standard")
    disabled = resolve_tool_surface("standard", comms_enabled=False)
    enabled = resolve_tool_surface("standard", comms_enabled=True)
    assert disabled == default
    assert set(PACK_TOOLS["peer_comms"]) == {"trw_send", "trw_inbox"}
    assert set(enabled.tools) - set(default.tools) == set(PACK_TOOLS["peer_comms"])
    assert set(default.tools) <= set(enabled.tools)
    assert enabled.packs == (*default.packs, "peer_comms")
    assert "peer_comms" not in enabled.decision  # off-list omits an on pack
    assert "comms_enabled=false" in disabled.decision
    assert "peer_comms" not in disabled.packs
    assert FLAG_GATED_PACKS.get("peer_comms") == "comms_enabled"
    assert not set(PACK_TOOLS["peer_comms"]) & set(KERNEL_TOOLS)


@pytest.mark.parametrize("enabled", [False, True])
def test_config_and_middleware_read_the_same_opt_in(enabled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    config = TRWConfig(comms_enabled=enabled, tool_resolution_mode="standard")
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    expected = resolve_tool_surface("standard", comms_enabled=enabled)
    resolved = SurfaceAuthorityMiddleware()._resolve()
    assert resolved is not None
    assert resolved.tools == frozenset(expected.tools) | ALWAYS_ON_TOOLS


def test_all_mode_unaffected_by_the_comms_flag() -> None:
    """``mode="all"`` already forces comms on (PRD-CORE-300 FR09); the explicit
    flag cannot narrow or further widen it."""
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    assert resolve_tool_surface("all", comms_enabled=False) == resolve_tool_surface("all", comms_enabled=True)


async def test_enabled_public_calls_repeat_without_needing_a_grant(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware

    joined_member(formation_env, "impl-1", "pin-opt-in")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-opt-in")
    config = enable_comms(monkeypatch)
    comms_server.add_middleware(SurfaceAuthorityMiddleware())
    for action in ("enroll", "heartbeat", "list", "heartbeat"):
        assert "trw_inbox" in {tool.name for tool in await comms_server.list_tools()}
        result = await comms_server.call_tool("trw_inbox", {"action": action})
        assert result.structured_content is not None
        assert result.structured_content["status"] == "ok"
    config.comms_enabled = False
    assert "trw_inbox" not in {tool.name for tool in await comms_server.list_tools()}
    denied = await comms_server.call_tool("trw_inbox", {"action": "heartbeat"})
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"


@pytest.mark.parametrize("mode", ["standard", "all"])
@pytest.mark.parametrize(
    "tool,action",
    [
        ("trw_send", None),
        ("trw_inbox", "fetch"),
        ("trw_inbox", "enroll"),
    ],
)
async def test_reviewer_still_refuses_comms_with_opt_in(
    comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch, mode: str, tool: str, action: str | None
) -> None:
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.surface_packs import REVIEWER_TOOLS

    config = TRWConfig(comms_enabled=True, surface_role="reviewer", tool_resolution_mode=mode)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    gate = SurfaceAuthorityMiddleware()
    resolved = gate._resolve()
    assert resolved is not None
    assert resolved.tools == REVIEWER_TOOLS
    comms_server.add_middleware(gate)
    assert await comms_server.list_tools() == []
    arguments = (
        {"action": action}
        if tool == "trw_inbox"
        else {"recipient_member_id": "other", "request_key": "blocked", "body": "untrusted"}
    )
    # Directly calling an excluded tool must be denied — the retargeted
    # positive control for "no grant path can widen the reviewer lane"
    # (PRD-CORE-300 S11b deleted the grant ledger entirely).
    denied = await comms_server.call_tool(tool, arguments)
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"


async def test_fr11_wait_seconds_parameter_does_not_grow_the_comms_pack_or_register_a_new_tool(
    comms_server: FastMCP,
) -> None:
    """PRD-CORE-274 Amendment 01: trw_inbox gained a PARAMETER, not the pack a member.

    The pack membership and tool count must stay exactly what they were before
    the amendment; a positive control (the parameter itself IS present) rules
    out this test silently passing because nothing was actually registered.
    """
    from trw_mcp.models.surface_packs import PACK_TOOLS

    assert PACK_TOOLS["peer_comms"] == ("trw_send", "trw_inbox")
    assert len(PACK_TOOLS["peer_comms"]) == 2
    tools = {tool.name: tool for tool in await comms_server.list_tools()}
    assert set(tools) & set(PACK_TOOLS["peer_comms"]) == set(PACK_TOOLS["peer_comms"])
    assert "wait_seconds" in tools["trw_inbox"].parameters["properties"]
