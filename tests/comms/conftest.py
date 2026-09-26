"""Shared plumbing for comms tests.

Everything here drives the REAL tool dispatch (``server.call_tool``) rather than
the underlying function, because the thing under test is what an MCP client can
actually reach — argument coercion, the closed action vocabulary and the
response projection included.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, write_pin
from trw_mcp.comms import _endpoints
from trw_mcp.formation import create, join
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools


@pytest.fixture
def comms_server() -> FastMCP:
    server = FastMCP("comms-test")
    register_swarm_comms_tools(server)
    return server


@pytest.fixture(autouse=True)
def _fresh_process_incarnations() -> Any:
    """Each test starts as a process that has never enrolled anything, nor sent guidance.

    Incarnations live in module state by design (a caller must not be able to
    name one), so without this a later test inherits an earlier test's claim
    and silently skips the collision path it meant to exercise.
    """
    from trw_mcp.comms import _guidance

    _endpoints._reset_process_incarnations_for_test()
    _guidance._reset_for_test()
    yield
    _endpoints._reset_process_incarnations_for_test()
    _guidance._reset_for_test()


#: PRD-CORE-274-FR18 decoration every public response carries; stripped where a test
#: pins the rest of a payload exactly (guidance itself is tested in test_guidance.py).
FR18_DECORATION = ("state", "guidance_version", "guidance")


def core(payload: dict[str, Any]) -> dict[str, Any]:
    """*payload* without the FR18 state/guidance decoration."""
    return {k: v for k, v in payload.items() if k not in FR18_DECORATION}


def call_peers(server: FastMCP, action: str = "list") -> dict[str, Any]:
    """Invoke a trw_inbox peer action through the real dispatch and return its payload."""
    result = asyncio.run(server.call_tool("trw_inbox", {"action": action}))
    payload = result.structured_content
    assert isinstance(payload, dict), f"expected a structured payload, got {type(payload)}"
    return payload


def enable_comms(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> TRWConfig:
    """Point get_config at a config with comms on, plus any field overrides."""
    config = TRWConfig(comms_enabled=True, **overrides)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    return config


def joined_member(fixture: FormationFixture, member_id: str, pin_key: str) -> Path:
    """Create the formation on first call, join *member_id*, and pin it."""
    manifest_path = fixture.manifest_path()
    if manifest_path.exists():
        from trw_mcp.formation import load

        loaded = load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
        assert loaded is not None
        formation_id = loaded.manifest.formation_id
    else:
        formation_id = create(fixture.orchestrator_run, fixture.payload(), trw_dir=fixture.trw_dir).formation_id
    run = fixture.member_runs[member_id]
    join(formation_id, member_id, run, pin_key=pin_key, trw_dir=fixture.trw_dir)
    write_pin(fixture, pin_key, run)
    return run
