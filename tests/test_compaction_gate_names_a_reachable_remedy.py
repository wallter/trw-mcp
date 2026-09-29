"""B71-112: the post-compaction gate never prescribes a tool the caller cannot call.

The gate's only remedy is ``trw_session_start``. It used to exempt the reviewer role alone; it now asks
the server-resolved surface, so any role or posture without that tool is exempt, and every posture that
IS gated has the remedy on its surface. A client that dropped the tool on its own side (Claude Code
after a mid-session MCP reconnect, TB-25) is invisible to the server, so the block message names the
reconnect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

_POSTURES = {
    # posture -> (TRW_SURFACE_ROLE, .trw/config.yaml body)
    "agent standard": ("", "tool_resolution_mode: standard\n"),
    "agent all": ("", "tool_resolution_mode: all\n"),
    "agent every flag on": ("", "comms_enabled: true\ndispatch_tools_exposed: true\nassess_enabled: true\n"),
    "agent every flag off": ("", "comms_enabled: false\ndispatch_tools_exposed: false\nassess_enabled: false\n"),
    "reviewer": ("reviewer", "tool_resolution_mode: all\n"),
}


async def _call(middleware: CeremonyMiddleware, session_ctx: FakeContext, tool: str) -> tuple[Any, int]:
    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return FakeToolResult(content=[TextContent(type="text", text="ok")])

    ctx = FakeMiddlewareContext(message=FakeMessage(name=tool), fastmcp_context=session_ctx)
    return await middleware.on_call_tool(ctx, call_next), calls  # type: ignore[arg-type]


@pytest.mark.parametrize("posture", sorted(_POSTURES))
async def test_a_gated_posture_holds_the_remedy_and_an_ungated_one_lacks_it(
    middleware: CeremonyMiddleware,
    session_ctx: FakeContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    posture: str,
) -> None:
    from trw_mcp.middleware.surface_authority import resolved_surface
    from trw_mcp.models.config import _reset_config

    role, config_yaml = _POSTURES[posture]
    trw_dir = _seed_compaction_marker(tmp_path)
    (trw_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    if role:
        monkeypatch.setenv("TRW_SURFACE_ROLE", role)
    else:
        monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _reset_config()
    try:
        out, calls = await _call(middleware, session_ctx, "trw_recall")
        remedy_reachable = "trw_session_start" in resolved_surface()
    finally:
        _reset_config()

    blocked = out.structured_content is not None and out.structured_content.get("error") == (
        "post_compaction_recovery_required"
    )
    # The invariant: the gate blocks exactly the postures whose surface holds its remedy.
    assert blocked is remedy_reachable
    assert blocked is (role != "reviewer")
    if blocked:
        assert calls == 0 and out.structured_content["remedy"] == "trw_session_start"
    else:
        assert calls == 1


async def test_a_surface_without_session_start_is_exempt_whatever_the_role(
    middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future role or posture that drops the tool is exempt without a second special case."""
    trw_dir = _seed_compaction_marker(tmp_path)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr(
        "trw_mcp.middleware.surface_authority.resolved_surface", lambda: frozenset({"trw_recall", "trw_code"})
    )

    out, calls = await _call(middleware, session_ctx, "trw_recall")

    assert calls == 1
    assert out.structured_content is None
    assert not any("trw_session_start" in getattr(block, "text", "") for block in out.content)


async def test_an_unresolvable_surface_leaves_the_gate_armed(
    middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exemption is granted by evidence, never by a fault (fail-closed)."""
    trw_dir = _seed_compaction_marker(tmp_path)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)

    def _boom() -> frozenset[str]:
        raise RuntimeError("surface resolution failed")

    monkeypatch.setattr("trw_mcp.middleware.surface_authority.resolved_surface", _boom)

    out, calls = await _call(middleware, session_ctx, "trw_recall")

    assert calls == 0
    assert out.structured_content["error"] == "post_compaction_recovery_required"


def test_the_block_message_names_the_reconnect_for_a_client_that_dropped_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.middleware import _compaction_gate_payload as payload

    monkeypatch.setattr(payload, "_read_marker_instant", lambda: ("2026-09-26T02:22:00+00:00", None))
    message = payload.build_compaction_block("trw_inbox", 1, 2).message

    assert "Not in your tool list? Reconnect the trw MCP server" in message
