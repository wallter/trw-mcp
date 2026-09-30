"""CODEX-P0-B: the reviewer surface of a REAL stdio server, probed exhaustively (tests and fixtures only).

``test_reviewer_posture_stdio_child.py`` proves the advertised catalogue equals ``REVIEWER_TOOLS`` and
that ONE forbidden tool is refused. This file closes the rest of the handoff's §B acceptance on the same
real ``python -m trw_mcp.server`` child in a throwaway project:

* every tool the unmarked server advertises but the reviewer may not use is absent from the reviewer's
  broad discovery (``tools/list``) AND refused on an exact-name call, with the typed denial and no write;
* a call to a tool name that does not exist fails visibly, never as a silent success;
* the reviewer's allowed tools are actually usable, so a bound that returned zero usable tools would fail.

Pinned identity: ``PINNED_REVIEWER_TOOLS`` below. A change to the reviewer surface must update it here
deliberately, so a widening cannot ride in on an edit to ``surface_packs.py`` alone.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._stdio_benchmark_support import build_temp_project
from tests._stdio_harness import stdio_import_skip_reason
from tests.test_reviewer_posture_stdio_child import _RoleHarness, _rpc, _tool_names
from trw_mcp.models.surface_packs import REVIEWER_TOOLS

PINNED_REVIEWER_TOOLS = frozenset({"trw_code", "trw_recall"})


@pytest.fixture(autouse=True)
def _require_stdio() -> None:
    reason = stdio_import_skip_reason()
    if reason is not None:  # pragma: no cover - environment guard
        pytest.skip(reason)  # skip-category: optional-dependency


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    return build_temp_project(tmp_path)


@pytest.fixture
def harnesses(project: tuple[Path, Path], tmp_path: Path) -> Iterator[tuple[_RoleHarness, _RoleHarness]]:
    root, user_dir = project
    agent = _RoleHarness(root, user_dir, tmp_path / "stderr-agent", None)
    reviewer = _RoleHarness(root, user_dir, tmp_path / "stderr-reviewer", "reviewer")
    try:
        yield agent, reviewer
    finally:
        agent.teardown()
        reviewer.teardown()


#: Observability a denied call is SUPPOSED to write: the denial is logged with the role (see
#: ``test_reviewer_surface_enforcement.py::test_reviewer_denial_is_logged_with_the_role``) and the MCP
#: security monitor records its argument baseline. Everything else under ``.trw`` is state a tool could mutate.
_OBSERVABILITY = ("context/", "security/", "logs/")


def _tree(root: Path) -> dict[str, str]:
    """Every non-observability file under the project's ``.trw`` with its content hash: any state write differs."""
    trw = root / ".trw"
    if not trw.is_dir():
        return {}
    files = (p for p in trw.rglob("*") if p.is_file())
    return {
        rel: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
        if not (rel := str(p.relative_to(trw))).startswith(_OBSERVABILITY)
    }


def test_the_reviewer_surface_is_the_pinned_set() -> None:
    assert frozenset(REVIEWER_TOOLS) == PINNED_REVIEWER_TOOLS


def test_every_non_reviewer_tool_is_hidden_and_refused_by_exact_name(
    harnesses: tuple[_RoleHarness, _RoleHarness], project: tuple[Path, Path]
) -> None:
    agent, reviewer = harnesses
    agent_server, _ = agent.cold_initialize("agent")
    forbidden = sorted(_tool_names(agent, agent_server) - PINNED_REVIEWER_TOOLS)
    assert forbidden, "the unmarked server advertised nothing beyond the reviewer set; the probe proves nothing"

    reviewer_server, _ = reviewer.cold_initialize("reviewer")
    assert not set(forbidden) & _tool_names(reviewer, reviewer_server)

    root, _ = project
    before = _tree(root)
    refused: dict[str, Any] = {}
    for tool in forbidden:
        reply = _rpc(reviewer, reviewer_server, "tools/call", {"name": tool, "arguments": {}})
        payload = (reply.get("result") or {}).get("structuredContent") or {}
        refused[tool] = payload.get("error_type") or reply.get("error") or reply.get("result")
    wrong = {tool: got for tool, got in refused.items() if got != "tool_not_in_reviewer_surface"}
    assert not wrong, f"not refused with the typed denial: {wrong}"
    assert _tree(root) == before, "a refused call still changed the project's .trw state"


def test_an_unknown_tool_name_is_refused_in_the_payload(harnesses: tuple[_RoleHarness, _RoleHarness]) -> None:
    _, reviewer = harnesses
    server, _ = reviewer.cold_initialize("reviewer")
    reply = _rpc(reviewer, server, "tools/call", {"name": "trw_no_such_tool", "arguments": {}})
    payload = (reply.get("result") or {}).get("structuredContent") or {}
    assert payload.get("error") == "mcp_security_blocked", reply


# CODEX-P0-B (reproduced 2026-09-30, fixed here): every refusal used to return ``isError: false``, so an MCP
# client trusting the protocol's error flag saw a blocked call as a SUCCESSFUL one.
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("trw_learn", {"summary": "x", "detail": "y"}), ("trw_deliver", {}), ("trw_no_such_tool", {})],
)
def test_a_refused_call_sets_the_protocol_error_flag(
    harnesses: tuple[_RoleHarness, _RoleHarness], tool: str, arguments: dict[str, Any]
) -> None:
    _, reviewer = harnesses
    server, _ = reviewer.cold_initialize("reviewer")
    reply = _rpc(reviewer, server, "tools/call", {"name": tool, "arguments": arguments})
    assert "error" in reply or (reply.get("result") or {}).get("isError") is True, reply


def test_the_reviewers_allowed_tools_are_usable(harnesses: tuple[_RoleHarness, _RoleHarness]) -> None:
    """A bound that left the reviewer zero USABLE tools would pass the two tests above; this one would not."""
    _, reviewer = harnesses
    server, _ = reviewer.cold_initialize("reviewer")
    assert _tool_names(reviewer, server) == set(PINNED_REVIEWER_TOOLS)
    recall = _rpc(reviewer, server, "tools/call", {"name": "trw_recall", "arguments": {"query": "*"}})
    result = recall.get("result") or {}
    assert "error" not in recall and result.get("isError") is not True, recall
    assert (result.get("structuredContent") or {}).get("error_type") != "tool_not_in_reviewer_surface"
