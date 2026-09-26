"""PRD-FIX-119 Slice A (FR01 + FR07) — trw_review is structurally reachable.

Before this suite, ``trw_review`` was in no never-hide set:

* ``SurfaceAuthorityMiddleware`` resolved a kernel-only 11-tool surface for any
  session without a pinned run (which is every session at MCP connect, because
  *tools/list* is served before ``trw_init`` can pin anything), and
* the (since deleted) phase-exposure layer exposed ``trw_review`` only under the
  ``REVIEW`` phase bucket — while ``trw_review`` is the ONLY writer of
  ``Phase.REVIEW`` (``tools/review.py``), so with ``phase_exposure_enabled: true``
  a session in IMPLEMENT could never reach REVIEW.

Meanwhile ``review_scope_block`` (``tools/_delivery_helpers``) is a NO_ESCAPE
delivery gate whose only named remedy is ``trw_review``.

FR01's ``RIGID_TOOLS`` never-hide set was superseded by PRD-CORE-300 S11b's flat
surface: ``trw_review`` moved straight into ``KERNEL_TOOLS``, so it is now
unconditionally on the surface rather than merely never-masked among masked
peers. FR07's phase cycle is closed by deleting phase exposure (PRD-CORE-300
S11a; see ``test_phase_matrix.py``). Every assertion here drives the REAL
middleware entrypoints — no stubbed resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.surface_authority import (
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS, KERNEL_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names

# ── Fakes (same shape as test_surface_authority_middleware.py) ──────────


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeRequestContext:
    session_id: str = "sess-fix119"


@dataclass
class _FakeContext:
    _session_id: str = "sess-fix119"
    request_context: _FakeRequestContext | None = field(default_factory=_FakeRequestContext)

    @property
    def session_id(self) -> str:
        return self._session_id


@dataclass
class _FakeMessage:
    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class _FakeMiddlewareContext:
    message: Any = None
    fastmcp_context: _FakeContext | None = None


@dataclass
class _FakeToolResult:
    content: list[Any] = field(default_factory=list)
    structured_content: dict[str, Any] | None = None


_EXECUTED = _FakeToolResult(content=[TextContent(type="text", text="executed")])


def _catalogue() -> list[_FakeTool]:
    """The full registered, publicly-eligible tool catalogue as Tool stubs."""
    return [_FakeTool(name=n) for n in sorted(eligible_tool_names())]


async def _list_via(mw: Any, ctx: _FakeMiddlewareContext) -> set[str]:
    async def call_next(_ctx: Any) -> Any:
        return _catalogue()

    return {t.name for t in await mw.on_list_tools(ctx, call_next)}


async def _call_via(mw: Any, tool_name: str, *, session_id: str = "sess-fix119") -> Any:
    """Dispatch ``tool_name`` through ``mw.on_call_tool``; return the result."""

    async def call_next(_ctx: Any) -> Any:
        return _EXECUTED

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(name=tool_name),
        fastmcp_context=_FakeContext(session_id),
    )
    return await mw.on_call_tool(ctx, call_next)


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    reset_surface_authority_state()
    yield
    reset_surface_authority_state()


# ── FR01: membership ────────────────────────────────────────────────────


def test_review_is_a_kernel_tool() -> None:
    """FR01, superseded by S11b: the never-hide guarantee is now unconditional
    membership in KERNEL_TOOLS rather than a separate RIGID_TOOLS set."""
    assert "trw_review" in KERNEL_TOOLS
    assert "trw_review" in ALWAYS_ON_TOOLS


def test_kernel_boundary_excludes_ordinary_pack_tools() -> None:
    """FR01 boundary: the version-pinned kernel does not become a dumping
    ground. ``trw_prd_validate`` also joined the kernel (S11b); a pack tool
    that never joined it (``trw_dispatch``) is the negative case."""
    assert "trw_prd_validate" in KERNEL_TOOLS
    assert "trw_dispatch" not in KERNEL_TOOLS


# ── FR01: the always-on surface ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_visible_with_every_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: review is reachable on the exact bounded always-on surface, with
    every pack flag off.

    CORE218 owns versioned kernel membership; this test owns the additional
    bootstrap members and must not duplicate a historical kernel count.
    """
    monkeypatch.setattr("trw_mcp.middleware.surface_authority._resolve_mode", lambda: "standard")

    class _StubConfig:
        comms_enabled = False
        dispatch_tools_exposed = False
        assess_enabled = False

    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _StubConfig())

    mw = SurfaceAuthorityMiddleware()
    names = await _list_via(mw, _FakeMiddlewareContext(fastmcp_context=_FakeContext()))

    assert "trw_review" in names
    assert names == ALWAYS_ON_TOOLS & set(eligible_tool_names())
    # Non-regression: the bounded surface is still bounded — a flag-gated pack
    # tool stays masked, so this is not "everything is visible".
    assert "trw_dispatch" not in names


@pytest.mark.asyncio
async def test_review_call_not_denied_with_no_config_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01 / Acceptance 1: a REAL surface-authority dispatch of ``trw_review``
    from a fresh session with no pin and no reconnect reaches the tool.

    Nothing is monkeypatched here except the project root: ``tool_resolution_mode``
    comes from a real config file — the exact live-session shape that session
    7ae2b12c hit (the surface no longer depends on ``task_type`` at all, so
    there is no pin to omit any more).
    """
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    (trw / "config.yaml").write_text("tool_resolution_mode: standard\n", encoding="utf-8")
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()

    mw = SurfaceAuthorityMiddleware()  # REAL — no seam monkeypatching
    result = await _call_via(mw, "trw_review", session_id="sess-nopin")

    assert result is _EXECUTED, "trw_review must dispatch with no pin or reconnect"
    # Control: the SAME session is still genuinely bounded — a flag-gated pack
    # tool whose flag is off is denied, so the pass above is not fail-open.
    denied = await _call_via(mw, "trw_dispatch", session_id="sess-nopin")
    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"

    _reset_config()


# FR07 (the phase-exposure REVIEW deadlock) is closed by deletion: phase
# exposure is gone (PRD-CORE-300 S11a) and test_phase_matrix.py proves every
# kernel tool, trw_review included, is listed and callable in all six phases.
