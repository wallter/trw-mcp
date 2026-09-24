"""PRD-CORE-281-FR01 — the dispatch pack is reachable, and OFF by default.

The defect this pins (measured 2026-09-17, trw-mcp 3.1.0, a live Claude Code
session): ``trw_dispatch`` / ``trw_dispatch_status`` were absent from the
session's tool list, a ToolSearch for the name returned nothing, and the bundled
``trw-delegate`` skill nevertheless instructed the agent to call them first.
They were masked, not missing — the ``dispatch`` pack is named by NO entry of
``STANDARD_TASK_PACKS`` (it is HIGH-RISK), so the resolved surface was kernel +
verification + the never-hide union: exactly 15 tools.

Three authorities disagreed, which is why one test here is not enough:
  * ``models/phase_policy._DEFAULT_SAFE_SET`` lists both tools as phase-agnostic
    "always visible" — but ``SurfaceAuthorityMiddleware`` runs BEFORE
    ``PhaseExposureMiddleware`` and masked them before that Safe Set was read;
  * ``models/config/_defaults.HIGH_RISK_PACKS`` says the pack is operator-gated;
  * the shipped skill says "call it first".

So the fix is an explicit operator opt-in, and these tests pin BOTH directions:
off by default (the gate still exists) and reachable when turned on (the skill's
instruction can be followed).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware.surface_authority import (
    _ALWAYS_EXPOSED,
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import HIGH_RISK_PACKS
from trw_mcp.models.surface_packs import PACK_TOOLS, STANDARD_TASK_PACKS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names, resolve_tool_surface
from trw_mcp.tools import phase_overrides

_MOD = "trw_mcp.middleware.surface_authority"

#: The exact tools the reported session could not see.
_DISPATCH_TOOLS = ("trw_dispatch", "trw_dispatch_status")


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeContext:
    _session_id: str = "sess-dispatch"

    @property
    def session_id(self) -> str:
        return self._session_id


@dataclass
class _FakeMiddlewareContext:
    message: Any = None
    fastmcp_context: _FakeContext | None = None


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    yield
    phase_overrides.reset_overrides()
    reset_surface_authority_state()


def _all_tools() -> list[_FakeTool]:
    return [_FakeTool(name=n) for n in sorted(eligible_tool_names())]


class _StubConfig:
    """Only the two attributes ``_resolve`` reads off the config."""

    def __init__(self, *, dispatch_tools_exposed: bool) -> None:
        self.comms_enabled = False
        self.dispatch_tools_exposed = dispatch_tools_exposed


async def _list_names(monkeypatch: pytest.MonkeyPatch, *, exposed: bool, task_type: str | None) -> set[str]:
    monkeypatch.setattr(f"{_MOD}._resolve_mode", lambda: "standard")
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: task_type)
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: _StubConfig(dispatch_tools_exposed=exposed),
    )

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    out = await SurfaceAuthorityMiddleware().on_list_tools(
        _FakeMiddlewareContext(fastmcp_context=_FakeContext()),  # type: ignore[arg-type]
        call_next,  # type: ignore[arg-type]
    )
    return {t.name for t in out}


# ── The regression itself ────────────────────────────────────────────────


@pytest.mark.parametrize("task_type", [None, *sorted(STANDARD_TASK_PACKS)])
def test_no_task_type_reaches_the_dispatch_pack_on_its_own(task_type: str | None) -> None:
    """The measured cause: no task type resolves the pack, so no session lists it.

    Derived from the live tables rather than asserted as a literal, so this goes
    red the moment a task type DOES name the pack — at which point the opt-in
    below is no longer the only path and this file needs revisiting.
    """
    resolved = set(resolve_tool_surface(task_type, "standard").tools) | _ALWAYS_EXPOSED
    assert not set(_DISPATCH_TOOLS) & resolved


def test_the_pack_is_declared_high_risk_so_the_default_stays_off() -> None:
    """The gate is policy, not an accident: keep the two statements agreeing."""
    assert "dispatch" in HIGH_RISK_PACKS
    assert TRWConfig().dispatch_tools_exposed is False


def test_opt_in_adds_exactly_the_dispatch_pack_and_nothing_else() -> None:
    before = set(resolve_tool_surface("coding", "standard").tools)
    after = set(resolve_tool_surface("coding", "standard", dispatch_enabled=True).tools)
    assert after - before == set(PACK_TOOLS["dispatch"])
    assert before - after == set()


def test_the_opt_in_is_recorded_in_the_resolution_decision() -> None:
    """A widening that is not visible in the decision string is a silent one."""
    resolution = resolve_tool_surface("coding", "standard", dispatch_enabled=True)
    assert "dispatch" in resolution.packs
    assert "dispatch_tools_exposed" in resolution.decision


def test_all_mode_does_not_need_the_opt_in() -> None:
    """The operator escape already exposed the pack; the flag must not narrow it."""
    tools = set(resolve_tool_surface("coding", "all", dispatch_enabled=False).tools)
    assert set(_DISPATCH_TOOLS) <= tools


# ── The config field is wired end to end (not a facade) ──────────────────


def test_config_resolution_path_honours_the_field() -> None:
    off = TRWConfig()
    on = TRWConfig(dispatch_tools_exposed=True)
    assert not set(_DISPATCH_TOOLS) & set(off.resolve_tool_surface_for_task("coding").tools)
    assert set(_DISPATCH_TOOLS) <= set(on.resolve_tool_surface_for_task("coding").tools)


def test_the_field_projects_into_the_dispatch_view_model() -> None:
    """``_sub_config`` copies by EXACT name — a missing mirror field silently
    projects the default instead of the operator's value."""
    assert TRWConfig(dispatch_tools_exposed=True).dispatch.dispatch_tools_exposed is True


# ── The middleware is the production consumer ────────────────────────────


@pytest.mark.asyncio
async def test_middleware_masks_the_dispatch_tools_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    names = await _list_names(monkeypatch, exposed=False, task_type=None)
    assert not set(_DISPATCH_TOOLS) & names
    # The reported session's exact surface size, so a silent widening elsewhere
    # is visible here too.
    assert len(names) == 14  # trw_learn_update merged into trw_learn (PRD-CORE-291)


@pytest.mark.asyncio
async def test_middleware_exposes_the_dispatch_tools_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    names = await _list_names(monkeypatch, exposed=True, task_type=None)
    assert set(_DISPATCH_TOOLS) <= names


@pytest.mark.asyncio
async def test_opting_in_never_widens_a_reviewer_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Containment beats convenience: a reviewer lane spawning dispatches is the
    recursion REVIEWER_TOOLS excludes the pack for."""
    monkeypatch.setattr(f"{_MOD}._is_reviewer_role", lambda: True)
    names = await _list_names(monkeypatch, exposed=True, task_type="coding")
    assert not set(_DISPATCH_TOOLS) & names


# ── The skill an agent reads must state the path ─────────────────────────


def test_the_delegate_skill_names_the_way_through_the_gate() -> None:
    """A skill that names a masked tool without naming the gate is the
    discoverability half of the reported defect."""
    skill = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "skills" / "trw-delegate" / "SKILL.md"
    content = skill.read_text(encoding="utf-8")
    assert "dispatch_tools_exposed" in content
    assert "trw_request_tool_access" in content
    # The grant is single-use; a skill that omits that sends an agent into a
    # poll loop that dies on the second call.
    assert "ONE call" in content
