"""CORE274: explicit comms opt-in composes with surface, role and phase gates."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import enable_comms, joined_member


@pytest.fixture(autouse=True)
def _reset_surface_and_grants() -> Iterator[None]:
    from trw_mcp.middleware.surface_authority import reset_surface_authority_state
    from trw_mcp.tools import phase_overrides

    reset_surface_authority_state()
    phase_overrides.reset_overrides()
    yield
    reset_surface_authority_state()
    phase_overrides.reset_overrides()


@pytest.mark.parametrize("task", [None, "coding", "research", "unknown", "unmapped-task"])
def test_enabled_resolver_adds_exactly_peer_pack_without_default_growth(task: str | None) -> None:
    from trw_mcp.models.surface_packs import KERNEL_TOOLS, PACK_TOOLS, STANDARD_TASK_PACKS
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    default = resolve_tool_surface(task)
    disabled = resolve_tool_surface(task, comms_enabled=False)
    enabled = resolve_tool_surface(task, comms_enabled=True)
    assert disabled == default
    assert set(PACK_TOOLS["peer_comms"]) == {"trw_peers", "trw_send", "trw_inbox"}
    assert set(enabled.tools) - set(default.tools) == set(PACK_TOOLS["peer_comms"])
    assert set(default.tools) <= set(enabled.tools)
    assert enabled.packs == (*default.packs, "peer_comms")
    assert "comms_enabled" in enabled.decision
    assert "peer_comms" in enabled.decision
    assert "peer_comms" not in {pack for packs in STANDARD_TASK_PACKS.values() for pack in packs}
    assert not set(PACK_TOOLS["peer_comms"]) & set(KERNEL_TOOLS)


@pytest.mark.parametrize("enabled", [False, True])
def test_config_and_middleware_read_the_same_opt_in(enabled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.middleware.surface_authority import _ALWAYS_EXPOSED, SurfaceAuthorityMiddleware
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    config = TRWConfig(comms_enabled=enabled, tool_resolution_mode="standard")
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    # No pin -> unknown fallback; do not stub either resolver or the gate.
    expected = resolve_tool_surface(None, comms_enabled=enabled)
    assert config.resolve_tool_surface_for_task(None) == expected
    resolved = SurfaceAuthorityMiddleware()._resolve(session_id="no-such-pin", fastmcp_context=None)
    assert resolved is not None
    assert resolved.tools == frozenset(expected.tools) | _ALWAYS_EXPOSED


def test_all_mode_unchanged_by_opt_in() -> None:
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    assert resolve_tool_surface("coding", "all", comms_enabled=True) == resolve_tool_surface("coding", "all")


async def test_enabled_public_calls_repeat_without_consumable_grants(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
    from trw_mcp.tools import phase_overrides

    joined_member(formation_env, "impl-1", "pin-opt-in")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-opt-in")
    config = enable_comms(monkeypatch)
    comms_server.add_middleware(SurfaceAuthorityMiddleware())
    for action in ("enroll", "heartbeat", "list", "heartbeat"):
        assert "trw_peers" in {tool.name for tool in await comms_server.list_tools()}
        result = await comms_server.call_tool("trw_peers", {"action": action})
        assert result.structured_content is not None
        assert result.structured_content["status"] == "ok"
        assert phase_overrides._overrides == {}
    config.comms_enabled = False
    assert "trw_peers" not in {tool.name for tool in await comms_server.list_tools()}
    denied = await comms_server.call_tool("trw_peers", {"action": "heartbeat"})
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"


@pytest.mark.parametrize("mode", ["standard", "all"])
@pytest.mark.parametrize("tool", ["trw_peers", "trw_send", "trw_inbox"])
async def test_reviewer_still_refuses_comms_with_opt_in_and_grant(
    comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch, mode: str, tool: str
) -> None:
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.surface_packs import REVIEWER_TOOLS
    from trw_mcp.tools import phase_overrides

    config = TRWConfig(comms_enabled=True, surface_role="reviewer", tool_resolution_mode=mode)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    gate = SurfaceAuthorityMiddleware()
    resolved = gate._resolve(session_id="reviewer", fastmcp_context=None)
    assert resolved is not None
    assert resolved.tools == REVIEWER_TOOLS
    phase_overrides.grant_override("reviewer", tool, reason="explicit positive override control")
    comms_server.add_middleware(gate)
    assert await comms_server.list_tools() == []
    arguments = (
        {"action": "enroll"}
        if tool == "trw_peers"
        else {"action": "fetch"}
        if tool == "trw_inbox"
        else {"recipient_member_id": "other", "request_key": "blocked", "body": "untrusted"}
    )
    denied = await comms_server.call_tool(tool, arguments)
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"
    assert phase_overrides.has_active_override("reviewer", tool)


async def test_opt_in_does_not_bypass_composed_phase_gate(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware
    from trw_mcp.models.phase_policy import PhaseToolPolicy

    joined_member(formation_env, "impl-1", "pin-phase")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-phase")
    enable_comms(monkeypatch)
    # Phase source is a separate input; both actual middleware gates compose.
    monkeypatch.setattr("trw_mcp.middleware.phase_exposure.resolve_active_phase", lambda **_: "IMPLEMENT")
    policy = PhaseToolPolicy(allowed_tools_by_phase={"IMPLEMENT": []}, safe_set=frozenset())
    phase = PhaseExposureMiddleware(enabled=True, policy=policy)
    comms_server.add_middleware(SurfaceAuthorityMiddleware())
    comms_server.add_middleware(phase)
    assert await comms_server.list_tools() == []
    refused = await comms_server.call_tool("trw_peers", {"action": "enroll"})
    assert refused.structured_content is not None
    assert refused.structured_content["error_type"] == "tool_not_in_phase"
    assert list(formation_env.project_root.rglob("comms.sqlite3")) == []
    # Explicit phase permission is necessary AND sufficient after surface opt-in.
    phase._policy_override = PhaseToolPolicy(allowed_tools_by_phase={"IMPLEMENT": ["trw_peers"]}, safe_set=frozenset())
    assert "trw_peers" in {tool.name for tool in await comms_server.list_tools()}
    accepted = await comms_server.call_tool("trw_peers", {"action": "enroll"})
    assert accepted.structured_content is not None
    assert accepted.structured_content["status"] == "ok"
