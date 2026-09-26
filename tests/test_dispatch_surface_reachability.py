"""PRD-CORE-281-FR01 — the dispatch pack is reachable, and OFF by default.

The defect this pins (measured 2026-09-17, trw-mcp 3.1.0, a live Claude Code
session): ``trw_dispatch`` and its then-separate status tool were absent from the
session's tool list, a ToolSearch for the name returned nothing, and the bundled
``trw-delegate`` skill nevertheless instructed the agent to call them first.
They were masked, not missing — the dispatch pack is gated by
``dispatch_tools_exposed`` (default off), one of :data:`FLAG_GATED_PACKS`.

So the fix is an explicit operator opt-in, and these tests pin BOTH directions:
off by default (the gate still exists) and reachable when turned on (the
skill's instruction can be followed) — including under ``mode="all"``, which
(PRD-CORE-300 FR09) does NOT widen the dispatch pack the way it does comms and
assess: process launching is never on by default in any mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware.surface_authority import (
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS, FLAG_GATED_PACKS, PACK_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names, resolve_tool_surface

_MOD = "trw_mcp.middleware.surface_authority"

#: The exact tool the reported session could not see.
_DISPATCH_TOOLS = ("trw_dispatch",)  # its status and evidence helpers are modes since PRD-CORE-300 S7


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
    reset_surface_authority_state()
    yield
    reset_surface_authority_state()


def _all_tools() -> list[_FakeTool]:
    return [_FakeTool(name=n) for n in sorted(eligible_tool_names())]


class _StubConfig:
    """Only the attributes ``_resolve`` reads off the config."""

    def __init__(self, *, dispatch_tools_exposed: bool) -> None:
        self.comms_enabled = False
        self.dispatch_tools_exposed = dispatch_tools_exposed
        self.assess_enabled = False


async def _list_names(monkeypatch: pytest.MonkeyPatch, *, exposed: bool) -> set[str]:
    monkeypatch.setattr(f"{_MOD}._resolve_mode", lambda: "standard")
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


def test_standard_mode_never_reaches_the_dispatch_pack_on_its_own() -> None:
    """The measured cause: with no flags, standard mode never resolves the pack."""
    resolved = set(resolve_tool_surface("standard").tools)
    assert not set(_DISPATCH_TOOLS) & resolved


def test_the_pack_is_flag_gated_so_the_default_stays_off() -> None:
    """The gate is policy, not an accident: keep the two statements agreeing."""
    assert "dispatch" in FLAG_GATED_PACKS
    assert FLAG_GATED_PACKS["dispatch"] == "dispatch_tools_exposed"
    assert TRWConfig().dispatch_tools_exposed is False


def test_opt_in_adds_exactly_the_dispatch_pack_and_nothing_else() -> None:
    before = set(resolve_tool_surface("standard").tools)
    after = set(resolve_tool_surface("standard", dispatch_enabled=True).tools)
    assert after - before == set(PACK_TOOLS["dispatch"])
    assert before - after == set()


def test_the_opt_in_is_recorded_in_the_resolution_decision() -> None:
    """A masked-off pack is not visible in the decision string is a silent one:
    the flag name and value must appear while the pack is off, and the pack
    must leave the "off" list once opted in."""
    off = resolve_tool_surface("standard", dispatch_enabled=False)
    on = resolve_tool_surface("standard", dispatch_enabled=True)
    assert "dispatch" not in off.packs
    assert "dispatch_tools_exposed=false" in off.decision
    assert "dispatch" in on.packs
    assert "dispatch_tools_exposed=false" not in on.decision


def test_all_mode_still_needs_the_opt_in() -> None:
    """PRD-CORE-300 FR09: the operator escape widens comms and assess but NEVER
    the dispatch pack — process launching stays gated in every mode."""
    tools = set(resolve_tool_surface("all", dispatch_enabled=False).tools)
    assert not set(_DISPATCH_TOOLS) & tools
    tools_with_flag = set(resolve_tool_surface("all", dispatch_enabled=True).tools)
    assert set(_DISPATCH_TOOLS) <= tools_with_flag


# ── The config field is wired end to end (not a facade) ──────────────────


def test_the_field_projects_into_the_dispatch_view_model() -> None:
    """``_sub_config`` copies by EXACT name — a missing mirror field silently
    projects the default instead of the operator's value."""
    assert TRWConfig(dispatch_tools_exposed=True).dispatch.dispatch_tools_exposed is True


# ── The middleware is the production consumer ────────────────────────────


@pytest.mark.asyncio
async def test_middleware_masks_the_dispatch_tools_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    names = await _list_names(monkeypatch, exposed=False)
    assert not set(_DISPATCH_TOOLS) & names
    # The reported session's exact surface size, so a silent widening elsewhere
    # is visible here too. Derived from the live source (ALWAYS_ON_TOOLS with
    # comms/assess also off in this stub), never a hand-picked literal.
    assert names == ALWAYS_ON_TOOLS & set(eligible_tool_names())


@pytest.mark.asyncio
async def test_middleware_exposes_the_dispatch_tools_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    names = await _list_names(monkeypatch, exposed=True)
    assert set(_DISPATCH_TOOLS) <= names


@pytest.mark.asyncio
async def test_opting_in_never_widens_a_reviewer_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Containment beats convenience: a reviewer lane spawning dispatches is the
    recursion REVIEWER_TOOLS excludes the pack for."""
    monkeypatch.setattr(f"{_MOD}._is_reviewer_role", lambda: True)
    names = await _list_names(monkeypatch, exposed=True)
    assert not set(_DISPATCH_TOOLS) & names


# ── The skill an agent reads must state the path ─────────────────────────


def test_the_delegate_skill_names_the_way_through_the_gate() -> None:
    """A skill that names a masked tool without naming the gate is the
    discoverability half of the reported defect."""
    skill = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "skills" / "trw-delegate" / "SKILL.md"
    content = skill.read_text(encoding="utf-8")
    assert "dispatch_tools_exposed" in content
    # There is no grant path any more (PRD-CORE-300 S11b): a skill that implies
    # one sends an agent looking for a tool that no longer exists.
    assert "no alternative grant path" in content
