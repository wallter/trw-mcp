"""PRD-SEC-015 — a REAL stdio child marked TRW_SURFACE_ROLE=reviewer is bounded.

This is the end of the chain WP2 builds: ``dispatch/_posture`` puts
``TRW_SURFACE_ROLE=reviewer`` into a child's MCP server configuration, and this
file spawns that server for real — a separate ``python -m trw_mcp.server``
process over stdio, against a throwaway project — to check what the server
ACTUALLY does with the marking.

Why out of process: ``test_reviewer_surface_enforcement.py`` already drives the
middleware hooks in-process with a fake context, which proves the resolver logic
and nothing about a live server's advertised catalogue. The claim an operator
cares about — "the reviewer child can call these nine tools and no others" — is
a property of the process, so it is measured on a process.

Nothing here runs a vendor CLI. The rendered codex/claude argv is asserted in
``test_dispatch_reviewer_posture.py``; the live FR-12 probe is an operator step.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from tests._stdio_benchmark_support import build_temp_project
from tests._stdio_harness import (
    ServerProcess,
    StdioServerHarness,
    stdio_import_skip_reason,
)
from trw_mcp.models.surface_packs import REVIEWER_TOOLS

# A tool the reviewer surface deliberately excludes because it WRITES the shared
# learnings store — the measured pollution path this bound exists to close.
_FORBIDDEN_TOOL = "trw_learn"

_READ_TIMEOUT_S = 100.0


class _RoleHarness(StdioServerHarness):
    """Spawn children under a declared ``TRW_SURFACE_ROLE``.

    Subclassed rather than changing ``spawn``: the harness is shared PRD-CORE-262
    infrastructure and its spawn path is what the handshake benchmark measures.
    Overriding ``child_env`` uses the additive ``extra`` parameter and leaves
    every other caller on the byte-identical environment it had.
    """

    def __init__(self, project_root: Path, user_dir: Path, stderr_dir: Path, role: str | None) -> None:
        super().__init__(project_root, user_dir, stderr_dir)
        self._role = role

    def child_env(self, session_id: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        overlay = dict(extra or {})
        if self._role is not None:
            overlay["TRW_SURFACE_ROLE"] = self._role
        return super().child_env(session_id, overlay)


def _rpc(harness: StdioServerHarness, server: ServerProcess, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One JSON-RPC round trip.

    Uses the harness's framing helpers directly because this file may extend the
    harness's ENVIRONMENT (the ``extra`` parameter above) but not its protocol
    surface; adding a ``list_tools`` method to shared benchmark infrastructure to
    serve one security test would widen a module three other files depend on.
    """
    request_id = server.next_id()
    harness._send(server, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    return harness._await_id(server, request_id, time.monotonic() + _READ_TIMEOUT_S)


def _tool_names(harness: StdioServerHarness, server: ServerProcess) -> set[str]:
    reply = _rpc(harness, server, "tools/list", {})
    assert "error" not in reply, reply
    return {tool["name"] for tool in reply["result"]["tools"]}


def _call(harness: StdioServerHarness, server: ServerProcess, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    reply = _rpc(harness, server, "tools/call", {"name": tool, "arguments": arguments})
    assert "error" not in reply, reply
    result = reply["result"]
    assert isinstance(result, dict)
    return result


@pytest.fixture
def temp_project(tmp_path: Path) -> tuple[Path, Path]:
    return build_temp_project(tmp_path)


def _harness(temp_project: tuple[Path, Path], tmp_path: Path, role: str | None) -> Iterator[_RoleHarness]:
    project, user_dir = temp_project
    harness = _RoleHarness(project, user_dir, tmp_path / f"stderr-{role or 'agent'}", role)
    try:
        yield harness
    finally:
        harness.teardown()


@pytest.fixture
def reviewer_harness(temp_project: tuple[Path, Path], tmp_path: Path) -> Iterator[_RoleHarness]:
    yield from _harness(temp_project, tmp_path, "reviewer")


@pytest.fixture
def agent_harness(temp_project: tuple[Path, Path], tmp_path: Path) -> Iterator[_RoleHarness]:
    yield from _harness(temp_project, tmp_path, None)


@pytest.fixture(autouse=True)
def _require_stdio() -> None:
    reason = stdio_import_skip_reason()
    if reason is not None:  # pragma: no cover - environment guard
        pytest.skip(reason)


def _learnings_files(project: Path) -> set[str]:
    root = project / ".trw" / "learnings"
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_reviewer_child_advertises_exactly_the_nine_reviewer_tools(reviewer_harness: _RoleHarness) -> None:
    server, _ = reviewer_harness.cold_initialize("reviewer")
    assert _tool_names(reviewer_harness, server) == set(REVIEWER_TOOLS)


def test_an_unmarked_child_of_the_same_project_is_not_bounded(
    agent_harness: _RoleHarness, reviewer_harness: _RoleHarness
) -> None:
    """The discriminator: same code, same project, only the marking differs.

    Without this control, the reviewer assertion above would also pass against a
    server that bounded EVERY session to nine tools, which would be a different
    (and far worse) bug wearing the same green tick.
    """
    agent_server, _ = agent_harness.cold_initialize("agent")
    agent_tools = _tool_names(agent_harness, agent_server)
    reviewer_server, _ = reviewer_harness.cold_initialize("reviewer")
    reviewer_tools = _tool_names(reviewer_harness, reviewer_server)
    assert agent_tools - reviewer_tools, "the unmarked child saw no tool the reviewer child was denied"
    assert _FORBIDDEN_TOOL in agent_tools
    assert _FORBIDDEN_TOOL not in reviewer_tools


def test_reviewer_child_refuses_a_forbidden_tool_with_a_typed_denial(
    reviewer_harness: _RoleHarness, temp_project: tuple[Path, Path]
) -> None:
    project, _ = temp_project
    before = _learnings_files(project)
    server, _ = reviewer_harness.cold_initialize("reviewer")
    result = _call(
        reviewer_harness,
        server,
        _FORBIDDEN_TOOL,
        {"summary": "reviewer should not be able to write this", "detail": "containment probe"},
    )
    payload = result.get("structuredContent") or {}
    assert payload.get("error_type") == "tool_not_in_reviewer_surface"
    assert payload.get("tool_name") == _FORBIDDEN_TOOL
    assert sorted(payload.get("allowed_tools", [])) == sorted(REVIEWER_TOOLS)
    # The denial names no route to widen the surface (FR04): a bound the bounded
    # lane is told how to lift is not a bound.
    assert "trw_request_tool_access" not in str(result)
    # …and nothing was written: containment, not a rejected-but-logged write.
    assert _learnings_files(project) == before
