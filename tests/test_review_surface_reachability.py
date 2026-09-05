"""PRD-FIX-119 Slice A (FR01 + FR07) — trw_review is structurally reachable.

Before this suite, ``trw_review`` was in no never-hide set:

* ``SurfaceAuthorityMiddleware`` resolved a kernel-only 11-tool surface for any
  session without a pinned run (which is every session at MCP connect, because
  *tools/list* is served before ``trw_init`` can pin anything), and
* ``PhaseExposureMiddleware`` exposed ``trw_review`` only under the ``REVIEW``
  phase bucket — while ``trw_review`` is the ONLY writer of ``Phase.REVIEW``
  (``tools/review.py``), so with ``phase_exposure_enabled: true`` a session in
  IMPLEMENT could never reach REVIEW.

Meanwhile ``review_scope_block`` (``tools/_delivery_helpers``) is a NO_ESCAPE
delivery gate whose only named remedy is ``trw_review``.

FR01 puts ``trw_review`` in ``RIGID_TOOLS``; FR07 is the regression proof that
this closes the phase cycle. Every assertion here drives the REAL middleware
entrypoints — no stubbed resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
from trw_mcp.middleware.surface_authority import (
    _ALWAYS_EXPOSED,
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY, RIGID_TOOLS, PhaseToolPolicy
from trw_mcp.models.surface_packs import KERNEL_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names, resolve_tool_surface
from trw_mcp.tools import phase_overrides

_SURFACE_MOD = "trw_mcp.middleware.surface_authority"
_PHASE_MOD = "trw_mcp.middleware.phase_exposure"

#: The six canonical phase labels FR07 requires the test to iterate.
_ALL_PHASES = ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER")


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
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    yield
    phase_overrides.reset_overrides()
    reset_surface_authority_state()


# ── FR01: membership ────────────────────────────────────────────────────


def test_review_is_a_rigid_tool() -> None:
    """FR01: ``trw_review`` is in the never-hide set, and the set is exactly the
    four tools FIX-119 sanctions — widening it further is a deliberate act, not
    a drift."""
    assert "trw_review" in RIGID_TOOLS
    assert RIGID_TOOLS == frozenset({"trw_session_start", "trw_deliver", "trw_build_check", "trw_review"})


def test_review_is_in_the_default_safe_set() -> None:
    """FR01: the never-hide invariant is stated as "rigid tools live in the Safe
    Set"; ``trw_review`` must satisfy it, so ``DEFAULT_PHASE_POLICY`` alone (the
    defaults-layer seed a profile-less deployment gets) already exposes it."""
    assert "trw_review" in DEFAULT_PHASE_POLICY.safe_set
    assert RIGID_TOOLS <= DEFAULT_PHASE_POLICY.safe_set


def test_kernel_is_untouched_and_prd_validate_stays_non_rigid() -> None:
    """FR01 boundary: the version-pinned kernel does NOT move, and the never-hide
    set does not become a dumping ground. ``trw_prd_validate`` is the worked
    negative case — it is named only by a NUDGE, never by a NO_ESCAPE gate, so it
    stays outside ``RIGID_TOOLS`` (PRD-FIX-119 §Out of Scope)."""
    assert "trw_review" not in KERNEL_TOOLS
    assert "trw_prd_validate" not in RIGID_TOOLS
    assert "trw_code_search" not in RIGID_TOOLS


# ── FR01: the kernel-only surface (no pinned run) ───────────────────────


@pytest.mark.asyncio
async def test_review_visible_on_kernel_only_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: a session with NO pinned run (i.e. every session at MCP connect)
    resolves a 14-tool surface that contains ``trw_review``.

    14 = the 9 KERNEL_TOOLS + trw_build_check + trw_review (both now DECLARED by
    the PRD-CORE-246-FR05 ``unknown`` fallback, not only never-hidden) +
    trw_init, trw_submit_feedback, and trw_prd_validate (the three bootstrap
    tools; trw_prd_validate added 2026-09-04 to fix a wiring defect where a
    coding-task session, and any trw-prd-groomer/trw-requirement-reviewer
    sub-agent it dispatches, could never reach the requirement-quality
    validator).
    """
    monkeypatch.setattr(f"{_SURFACE_MOD}._resolve_mode", lambda: "standard")
    monkeypatch.setattr(f"{_SURFACE_MOD}.resolve_task_type", lambda **_: None)

    mw = SurfaceAuthorityMiddleware()
    names = await _list_via(mw, _FakeMiddlewareContext(fastmcp_context=_FakeContext()))

    assert "trw_review" in names
    assert names == set(KERNEL_TOOLS) | {
        "trw_build_check",
        "trw_init",
        "trw_review",
        "trw_submit_feedback",
        "trw_prd_validate",
    }
    assert len(names) == 14
    # Non-regression: the bounded surface is still bounded — a pack tool that is
    # NOT never-hidden stays masked, so this is not "everything is visible".
    assert "trw_code_search" not in names


def test_coding_surface_size_is_unchanged_by_fr01() -> None:
    """FR01 boundary: ``trw_review`` was ALREADY in the ``verification`` pack, so
    the ``coding`` surface must not grow on its account — union membership is
    idempotent. PRD-CORE-246-FR06 added exactly ONE tool (``trw_submit_feedback``)
    to the never-hide set (16 -> 17); the 2026-09-04 wiring-defect fix added a
    second (``trw_prd_validate``, 17 -> 18) so a coding-task session (and any
    sub-agent it dispatches) can reach the requirement-quality validator."""
    coding = (set(resolve_tool_surface("coding", "standard").tools) | _ALWAYS_EXPOSED) & set(eligible_tool_names())
    assert "trw_review" in coding
    assert "trw_prd_validate" in coding
    assert len(coding) == 18
    assert len(coding - {"trw_submit_feedback", "trw_prd_validate"}) == 16, (
        "FR06 + the 2026-09-04 fix added exactly two tools, not a class of them"
    )


@pytest.mark.asyncio
async def test_review_call_not_denied_without_a_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01 / Acceptance 1: a REAL surface-authority dispatch of ``trw_review``
    from a session with no pin, no grant and no reconnect reaches the tool.

    Nothing is monkeypatched here except the project root: ``tool_resolution_mode``
    comes from a real config file and ``task_type`` is resolved genuinely (to
    ``None``, because no run is pinned) — the exact live-session shape that
    session 7ae2b12c hit.
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

    assert result is _EXECUTED, "trw_review must dispatch with no pin, grant or reconnect"
    # Control: the SAME unpinned session is still genuinely bounded — a pack tool
    # outside the never-hide set is denied, so the pass above is not fail-open.
    denied = await _call_via(mw, "trw_code_search", session_id="sess-nopin")
    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"


# ── FR07: the phase-exposure REVIEW deadlock ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", _ALL_PHASES)
async def test_review_reachable_under_all_six_phases(phase: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07: with phase exposure ON, ``trw_review`` is listed AND callable in
    every one of the six phases — including IMPLEMENT, the phase a session must
    be able to leave.

    The policy handed to the middleware deliberately names ``trw_review`` in NO
    bucket and NO safe set, so the only thing that can make it visible is the
    ``| RIGID_TOOLS`` union. That isolates FR01 as the mechanism.
    """
    starved = PhaseToolPolicy(
        allowed_tools_by_phase={p: ["trw_checkpoint"] for p in _ALL_PHASES},
        safe_set=frozenset({"trw_status"}),
    )
    assert "trw_review" not in starved.list_for(phase), "precondition: the policy hides it"

    mw = PhaseExposureMiddleware(enabled=True, policy=starved)
    monkeypatch.setattr(f"{_PHASE_MOD}.resolve_active_phase", lambda **_: phase)

    names = await _list_via(mw, _FakeMiddlewareContext(fastmcp_context=_FakeContext()))
    assert "trw_review" in names, f"trw_review hidden in {phase}"

    result = await _call_via(mw, "trw_review")
    assert result is _EXECUTED, f"trw_review denied in {phase}"

    # Control: phase masking is still doing its job in this very phase.
    assert "trw_prd_create" not in names
    denied = await _call_via(mw, "trw_prd_create")
    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_phase"


@pytest.mark.asyncio
async def test_review_reachable_with_phase_exposure_enabled_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR07: the flag is read from CONFIG (``phase_exposure_enabled: true``), not
    from a constructor argument, and the production policy-resolution path (a
    resolved profile, not an injected policy) still exposes ``trw_review`` in
    IMPLEMENT — the phase where the deadlock bit.
    """
    from trw_mcp.models.config import _reset_config, get_config

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    (trw / "config.yaml").write_text("phase_exposure_enabled: true\n", encoding="utf-8")
    _reset_config()
    assert get_config().phase_exposure_enabled is True, "precondition: the flag is really on"

    mw = PhaseExposureMiddleware()  # enabled resolved from config, policy from the profile
    assert mw._enabled is True
    monkeypatch.setattr(f"{_PHASE_MOD}.resolve_active_phase", lambda **_: "IMPLEMENT")

    names = await _list_via(mw, _FakeMiddlewareContext(fastmcp_context=_FakeContext()))
    assert "trw_review" in names
    # The flag is genuinely masking (otherwise this proves nothing).
    assert "trw_prd_create" not in names
    assert await _call_via(mw, "trw_review") is _EXECUTED


@pytest.mark.asyncio
@pytest.mark.parametrize("phase_exposure_enabled", [False, True])
async def test_review_survives_the_composed_middleware_chain(
    phase_exposure_enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR01+FR07 composed, in production chain order (surface authority narrows
    to the task packs first, phase exposure then narrows to the phase subset).

    An unpinned session in IMPLEMENT must still see and dispatch ``trw_review``
    with the phase flag at EITHER setting — the PRD's blocking reachability layer.
    """
    monkeypatch.setattr(f"{_SURFACE_MOD}._resolve_mode", lambda: "standard")
    monkeypatch.setattr(f"{_SURFACE_MOD}.resolve_task_type", lambda **_: None)
    monkeypatch.setattr(f"{_PHASE_MOD}.resolve_active_phase", lambda **_: "IMPLEMENT")

    surface = SurfaceAuthorityMiddleware()
    phase = PhaseExposureMiddleware(enabled=phase_exposure_enabled, policy=DEFAULT_PHASE_POLICY)
    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())

    async def surface_then_full(_ctx: Any) -> Any:
        return _catalogue()

    # Chain: surface authority output feeds phase exposure (FR08 ordering).
    surfaced = await surface.on_list_tools(ctx, surface_then_full)  # type: ignore[arg-type]

    async def phase_call_next(_ctx: Any) -> Any:
        return surfaced

    composed = {t.name for t in await phase.on_list_tools(ctx, phase_call_next)}  # type: ignore[arg-type]
    assert "trw_review" in composed
    assert "trw_code_search" not in composed  # the composition still bounds

    # And the call path agrees through BOTH gates.
    assert await _call_via(surface, "trw_review") is _EXECUTED
    assert await _call_via(phase, "trw_review") is _EXECUTED
