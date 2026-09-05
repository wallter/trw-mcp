"""PRD-SEC-015 FR05 — a reviewer is exempt from ceremony it cannot satisfy.

The post-compaction gate's only remedy is ``trw_session_start``, which the
reviewer surface deliberately denies (FR01). A reviewer also never had context to
recover: it is a stateless, single-purpose lane. Gating it can therefore only
deadlock it or degrade through the block bound, while producing zero context
integrity — so the gate and the ceremony warning both skip the role.

Additive to PRD-CORE-258, which owns the gate's error name and payload: this file
asserts only the exemption and touches none of its error-string assertions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from tests._test_ceremony_middleware_gate_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeToolResult,
    _clean_state,  # noqa: F401  # autouse: resets middleware module state per test
    _seed_compaction_marker,
    middleware,  # noqa: F401
    session_ctx,  # noqa: F401
)
from trw_mcp.middleware.ceremony import CeremonyMiddleware

_MOD = "trw_mcp.middleware.ceremony"


def _as_reviewer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    class _Cfg:
        surface_role = "reviewer"

    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Cfg())


async def test_reviewer_role_bypasses_compaction_gate_and_warning(
    middleware: CeremonyMiddleware,
    session_ctx: FakeContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the marker armed, a REVIEWER_TOOLS call executes and returns the
    unmodified tool result — no gate payload, no prepended warning block."""
    _as_reviewer(monkeypatch)
    _seed_compaction_marker(tmp_path)
    tool_result = FakeToolResult(content=[TextContent(type="text", text="recall ok")])
    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return tool_result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
    with patch(f"{_MOD}._is_compaction_gate_required", return_value=True):
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert calls == 1, "the reviewer's call must reach the tool"
    assert out.structured_content is None
    assert len(out.content) == 1
    assert isinstance(out.content[0], TextContent) and out.content[0].text == "recall ok"


async def test_an_inactive_reviewer_session_gets_no_ceremony_warning(
    middleware: CeremonyMiddleware,
    session_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warning tells a caller to run a ceremony the reviewer cannot run."""
    _as_reviewer(monkeypatch)
    tool_result = FakeToolResult(content=[TextContent(type="text", text="search ok")])

    async def call_next(_ctx: Any) -> Any:
        return tool_result

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_code_search"), fastmcp_context=session_ctx)
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert len(out.content) == 1
    assert isinstance(out.content[0], TextContent) and out.content[0].text == "search ok"


async def test_without_the_role_the_gate_and_the_warning_behave_exactly_as_before(
    middleware: CeremonyMiddleware,
    session_ctx: FakeContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity + migration proof: with no reviewer role the SAME inputs still
    gate the call and still prepend the warning."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _seed_compaction_marker(tmp_path)
    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return FakeToolResult(content=[TextContent(type="text", text="recall ok")])

    gated = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
    with patch(f"{_MOD}._is_compaction_gate_required", return_value=True):
        blocked = await middleware.on_call_tool(gated, call_next)  # type: ignore[arg-type]

    assert calls == 0
    assert blocked.structured_content is not None

    warned = FakeMiddlewareContext(message=FakeMessage(name="some_other_tool"), fastmcp_context=session_ctx)
    with patch(f"{_MOD}._is_compaction_gate_required", return_value=False):
        out = await middleware.on_call_tool(warned, call_next)  # type: ignore[arg-type]

    assert len(out.content) == 2, "the ceremony warning is still prepended for an agent session"


async def test_a_config_fault_does_not_grant_the_exemption(
    middleware: CeremonyMiddleware,
    session_ctx: FakeContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative: the exemption is a GRANT, so it must fail closed — an unreadable
    config must not turn every session into an exempt 'reviewer'."""

    def _raise() -> object:
        raise RuntimeError("config is unreadable")

    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", _raise)
    _seed_compaction_marker(tmp_path)
    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return FakeToolResult(content=[TextContent(type="text", text="recall ok")])

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
    with patch(f"{_MOD}._is_compaction_gate_required", return_value=True):
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert calls == 0, "a config fault must not exempt an ordinary session"
    assert out.structured_content is not None


async def test_the_env_marker_alone_grants_the_ceremony_exemption(
    middleware: CeremonyMiddleware,
    session_ctx: FakeContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exemption travels with the PROCESS, not with the reviewed repository's
    config: a dispatched child is marked by its spawner's environment."""
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    class _AgentCfg:
        surface_role = "agent"

    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _AgentCfg())
    _seed_compaction_marker(tmp_path)
    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return FakeToolResult(content=[TextContent(type="text", text="recall ok")])

    ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
    with patch(f"{_MOD}._is_compaction_gate_required", return_value=True):
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert calls == 1
    assert out.structured_content is None
