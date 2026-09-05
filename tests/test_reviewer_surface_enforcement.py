"""PRD-SEC-015 FR03/FR04 + NFR01/NFR02/NFR03 — the reviewer bound is server-side.

Enters through the REAL ``on_list_tools`` / ``on_call_tool`` hooks (never a
monkeypatched ``_resolve``), mirroring ``test_surface_authority_middleware.py``:
the claim under test is what a dispatched child's MCP server actually does, so a
stubbed resolver would prove nothing.

Layer attribution: this file is the attribution test for **Layer A** (the server
role). Layer B (the Codex ``enabled_tools`` allowlist) is attributed separately by
the FR12 live probe, so a regression in either layer is identified rather than
masked by the other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from trw_mcp.middleware.surface_authority import (
    _ALWAYS_EXPOSED,
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.surface_packs import REVIEWER_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names
from trw_mcp.tools import phase_overrides

_MOD = "trw_mcp.middleware.surface_authority"


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeContext:
    _session_id: str = "reviewer-sess"

    @property
    def session_id(self) -> str:
        return self._session_id


@dataclass
class _FakeMessage:
    name: str


@dataclass
class _FakeMiddlewareContext:
    message: Any = None
    fastmcp_context: _FakeContext | None = None


_EXECUTED = object()


def _all_tools() -> list[_FakeTool]:
    return [_FakeTool(name=n) for n in sorted(eligible_tool_names())]


async def _execute(_ctx: Any) -> Any:
    return _EXECUTED


@pytest.fixture
def middleware() -> SurfaceAuthorityMiddleware:
    return SurfaceAuthorityMiddleware()


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    yield
    phase_overrides.reset_overrides()
    reset_surface_authority_state()


def _reviewer(monkeypatch: pytest.MonkeyPatch, *, mode: str = "standard", task_type: str | None = None) -> None:
    """Select the reviewer role through the CONFIG field, as production does."""

    class _Cfg:
        surface_role = "reviewer"
        tool_resolution_mode = mode

    monkeypatch.setattr(f"{_MOD}.get_config", lambda: _Cfg(), raising=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Cfg())
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: task_type)


# ── FR03: the role dominates mode, packs, and the never-hide union ──────


@pytest.mark.parametrize("mode", ["standard", "all"])
@pytest.mark.parametrize("task_type", [None, "coding", "research", "docs", "eval", "rca", "planning", "unknown"])
async def test_reviewer_role_overrides_mode_all_and_task_packs(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch, mode: str, task_type: str | None
) -> None:
    """Every (mode, task_type) permutation resolves to exactly REVIEWER_TOOLS.

    ``mode="all"`` is an operator widening of an agent's OWN session; the
    reviewer role is containment of a SUBORDINATE process, so honouring "all"
    would let the audited project's config un-bound the lane auditing it.
    """
    _reviewer(monkeypatch, mode=mode, task_type=task_type)
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    advertised = {t.name for t in await middleware.on_list_tools(ctx, call_next)}  # type: ignore[arg-type]

    assert advertised == set(REVIEWER_TOOLS)
    # The never-hide union is BYPASSED, not subtracted from: it grew on
    # 2026-09-04 (trw_prd_validate) and a subtractive design would re-widen
    # every reviewer surface on the next such addition.
    assert not (advertised & (_ALWAYS_EXPOSED - REVIEWER_TOOLS))
    assert "trw_prd_validate" not in advertised  # bootstrap never-hide member
    assert "trw_build_check" not in advertised  # RIGID member
    assert "trw_request_tool_access" not in advertised  # kernel member


async def test_reviewer_list_is_sorted_reviewer_tools_through_the_real_chain(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewer(monkeypatch)
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

    assert [t.name for t in out] == sorted(REVIEWER_TOOLS)


# ── FR04 + NFR03: exhaustive denial over the whole registered surface ───


@pytest.mark.parametrize("tool_name", sorted(eligible_tool_names()))
async def test_every_write_tool_is_denied_under_reviewer_role(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """NFR03: proven over EVERY registered public tool, not a sampled list —
    a newly registered tool is denied by default."""
    _reviewer(monkeypatch, mode="all", task_type="coding")
    ctx = _FakeMiddlewareContext(message=_FakeMessage(tool_name), fastmcp_context=_FakeContext())

    result = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    if tool_name in REVIEWER_TOOLS:
        assert result is _EXECUTED
        return
    payload = result.structured_content
    assert payload is not None
    assert payload["error_type"] == "tool_not_in_reviewer_surface"
    assert payload["tool_name"] == tool_name
    assert payload["surface_role"] == "reviewer"
    assert payload["allowed_tools"] == sorted(REVIEWER_TOOLS)
    # US-002: the bound must be unreachable from inside the bounded lane, so the
    # denial names no escalation path at all.
    assert "override_hint" not in payload
    text = "".join(getattr(block, "text", "") for block in result.content)
    # Naming the tool that was denied is not an escalation path; OFFERING the
    # escalation primitive or the operator mode switch is. Strip the denied
    # name first so the trw_request_tool_access case still proves the point.
    offered = text.replace(tool_name, "<denied>")
    assert "trw_request_tool_access" not in offered
    assert "tool_resolution_mode" not in offered


async def test_reviewer_denial_is_logged_with_the_role(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Containment is observable, not inferred (FR04)."""
    _reviewer(monkeypatch)
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())

    with capture_logs() as logs:
        await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    denied = [entry for entry in logs if entry["event"] == "surface_authority_call_denied"]
    assert denied and denied[0]["surface_role"] == "reviewer"
    assert denied[0]["tool"] == "trw_deliver"


async def test_a_planted_override_grant_does_not_unmask_a_write_tool(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR04: the single-use grant store is not consulted under the reviewer role.

    ``trw_request_tool_access`` grants any masked tool for a >=20-char reason, so
    a bound that honoured grants would be self-escalatable by construction.
    """
    _reviewer(monkeypatch)
    phase_overrides.grant_override("reviewer-sess", "trw_learn", reason="a deliberately long planted reason")
    assert phase_overrides.has_active_override("reviewer-sess", "trw_learn")
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_learn"), fastmcp_context=_FakeContext())

    result = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    assert result is not _EXECUTED
    assert result.structured_content is not None
    assert result.structured_content["error_type"] == "tool_not_in_reviewer_surface"
    # The grant is not even CONSUMED — a reviewer denial must not silently burn
    # the parent session's single-use grant.
    assert phase_overrides.has_active_override("reviewer-sess", "trw_learn")


async def test_a_planted_grant_does_not_widen_the_reviewer_list(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reviewer(monkeypatch)
    phase_overrides.grant_override("reviewer-sess", "trw_deliver", reason="a deliberately long planted reason")
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    advertised = {t.name for t in await middleware.on_list_tools(ctx, call_next)}  # type: ignore[arg-type]

    assert advertised == set(REVIEWER_TOOLS)


# ── NFR01: the reviewer branch does strictly LESS work than an agent call ──


async def test_reviewer_resolution_adds_no_io(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The role is a cached-singleton attribute read: the branch returns BEFORE
    ``resolve_task_type`` (which reads the pinned run's meta/run.yaml from disk)
    and before the mode lookup. Both seams are replaced with raisers, so any
    reordering that reintroduces the I/O fails loudly rather than slowly."""

    class _Cfg:
        surface_role = "reviewer"
        tool_resolution_mode = "standard"

    def _boom(**_: object) -> object:
        raise AssertionError("reviewer resolution must not touch this seam")

    monkeypatch.setattr(f"{_MOD}.get_config", lambda: _Cfg(), raising=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Cfg())
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", _boom)
    monkeypatch.setattr(f"{_MOD}._resolve_mode", _boom)

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_recall"), fastmcp_context=_FakeContext())
    assert await middleware.on_call_tool(ctx, _execute) is _EXECUTED  # type: ignore[arg-type]


# ── NFR02: fail-CLOSED under the reviewer marker, fail-open without it ──


async def test_reviewer_marked_process_fails_closed_on_resolution_error(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fault in the resolution path must not silently un-bound the lane.

    The raiser here is the SESSION-ID lookup, i.e. a fault that happens before
    any role decision could be made — the one shape that still reaches this
    handler now that the role itself is read from the environment first. The
    predicate that decides the posture reads ``os.environ`` directly, never the
    config object that may have just failed. A bricked reviewer is a lost second
    opinion; an un-bounded reviewer is an authorization bypass, so availability
    loses to containment for a subordinate process and the PRD accepts
    "the reviewer returns only denials".
    """

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("session resolution is broken")

    monkeypatch.setattr(f"{_MOD}.safe_session_id_from_context", _raise)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())

    with capture_logs() as logs:
        result = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    assert result is not _EXECUTED
    assert result.structured_content is not None
    assert result.structured_content["error_type"] == "tool_not_in_reviewer_surface"
    assert any(entry.get("outcome") == "fail_closed_reviewer" for entry in logs), logs


async def test_the_same_fault_without_the_env_marker_still_fails_open(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-closed inversion is scoped to reviewer-marked processes and
    nothing else — the PRD-CORE-218 contract is unchanged for every session."""

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("session resolution is broken")

    monkeypatch.setattr(f"{_MOD}.safe_session_id_from_context", _raise)
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())

    assert await middleware.on_call_tool(ctx, _execute) is _EXECUTED  # type: ignore[arg-type]


async def test_a_raising_config_denies_under_the_env_marker(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PRD's own NFR02 case: config resolution raises inside a reviewer
    process. Containment holds WITHOUT reaching the fail-closed handler at all,
    because the role is decided from the environment before the config layer is
    consulted — a stronger property than recovering from the fault."""

    def _raise() -> str:
        raise RuntimeError("config is unreadable")

    monkeypatch.setattr(f"{_MOD}._resolve_mode", _raise)
    monkeypatch.setattr("trw_mcp.models.config.get_config", _raise)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_learn"), fastmcp_context=_FakeContext())

    result = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    assert result is not _EXECUTED
    assert result.structured_content is not None
    assert result.structured_content["error_type"] == "tool_not_in_reviewer_surface"


async def test_reviewer_marked_list_fails_closed_to_the_reviewer_intersection(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list path narrows to the reviewer intersection rather than exposing
    the full catalogue, for the same reason the call path denies."""

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("session resolution is broken")

    monkeypatch.setattr(f"{_MOD}.safe_session_id_from_context", _raise)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]

    assert {t.name for t in out} == set(REVIEWER_TOOLS)


async def test_without_the_env_marker_a_resolution_error_still_fails_open(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin on the PRD-CORE-218 contract: the fail-closed inversion is
    scoped to reviewer-marked processes and nothing else."""

    def _raise() -> str:
        raise RuntimeError("config is unreadable")

    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr(f"{_MOD}._resolve_mode", _raise)
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())

    assert await middleware.on_call_tool(ctx, _execute) is _EXECUTED  # type: ignore[arg-type]

    tools = _all_tools()  # and the list path still exposes the full catalogue

    async def call_next(_ctx: Any) -> Any:
        return tools

    listed = await middleware.on_list_tools(_FakeMiddlewareContext(fastmcp_context=_FakeContext()), call_next)  # type: ignore[arg-type]
    assert {t.name for t in listed} == {t.name for t in tools}


def test_the_env_marker_is_case_insensitive_and_ignores_a_bogus_value() -> None:
    """The fail-closed marker is the RAW env string, so its parsing is its own
    contract: only 'reviewer' arms it, and a bogus value never does."""
    from trw_mcp.middleware.surface_authority import _env_marks_reviewer

    before = os.environ.get("TRW_SURFACE_ROLE")
    try:
        for value, expected in (
            ("reviewer", True),
            ("Reviewer", True),
            (" reviewer ", True),
            ("agent", False),
            ("bogus", False),
            ("", False),
        ):
            os.environ["TRW_SURFACE_ROLE"] = value
            assert _env_marks_reviewer() is expected, value
        del os.environ["TRW_SURFACE_ROLE"]
        assert _env_marks_reviewer() is False
    finally:
        if before is None:
            os.environ.pop("TRW_SURFACE_ROLE", None)
        else:
            os.environ["TRW_SURFACE_ROLE"] = before


# ── NFR03 / OQ-003: the three retained telemetry appends stay unsuppressed ──


def test_no_suppression_branch_exists_for_the_retained_telemetry_appends() -> None:
    """DECIDED (OQ-003, corrected by the round-2 audit's Row 6): only
    ``trw_before_edit_hint`` and ``trw_codebase_risk_report`` carry an
    emit_tool_call / emit_hint_delivered append -- both, per
    ``channels/_distill_telemetry.py``'s own module docstring, write to the
    SAME sink (``.trw/telemetry/channel-events.jsonl``). ``trw_before_edit_hint_batch``
    was PREVIOUSLY (mis)claimed as a third retained writer here and in
    NFR03/CHANGELOG; it has no emission seam at all (``before_edit_hint_batch.py``
    imports neither ``emit_tool_call`` nor ``emit_hint_delivered``) -- the claim
    was corrected to match reality rather than a seam being wired to match the
    claim, since inventing a new write in a safety-critical reviewer-role PRD
    needs its own reviewed design, not a same-PRD patch-up.

    These two are local, append-only diagnostics with no agent-authored
    content and no shared-truth destination, and suppressing them would blind
    the very telemetry that measures whether the bound works.

    Asserted structurally because the append happens INSIDE the tool, where the
    middleware never reaches: the failure mode this guards is someone adding a
    role-conditional there, which no middleware-level behavioural test can see.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    modules = [
        src / "tools" / "before_edit_hint.py",
        src / "tools" / "codebase_risk_report.py",
        src / "channels" / "_distill_telemetry.py",
    ]
    for module in modules:
        text = module.read_text(encoding="utf-8")
        assert "surface_role" not in text, f"{module.name} gained a role-conditional"
        assert "REVIEWER_TOOLS" not in text, f"{module.name} gained a role-conditional"
        assert "TRW_SURFACE_ROLE" not in text, f"{module.name} gained a role-conditional"
    # Non-vacuity: the emit seams these modules must keep are actually there.
    assert "emit_hint_delivered" in modules[0].read_text(encoding="utf-8")
    assert "emit_tool_call" in modules[1].read_text(encoding="utf-8")


async def test_a_reviewer_call_to_a_telemetry_emitting_tool_still_executes(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The middleware half of the same decision: all three remain callable
    under the reviewer role (proving the surface never blocks them), though
    only ``trw_before_edit_hint`` and ``trw_codebase_risk_report`` actually
    carry a telemetry append -- see the corrected claim above."""
    _reviewer(monkeypatch)
    for tool_name in ("trw_before_edit_hint", "trw_before_edit_hint_batch", "trw_codebase_risk_report"):
        ctx = _FakeMiddlewareContext(message=_FakeMessage(tool_name), fastmcp_context=_FakeContext())
        assert await middleware.on_call_tool(ctx, _execute) is _EXECUTED, tool_name  # type: ignore[arg-type]


# ── FR14: the env declaration is authoritative, and sufficient on its own ──


async def test_the_env_marker_alone_bounds_a_session_with_a_hostile_config(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer inspects a repository it does not control, and that repository
    ships the ``.trw/config.yaml`` the loader merges. The role is therefore read
    from the SPAWNING process's environment first: a config that declares
    ``surface_role: agent`` and ``tool_resolution_mode: all`` — the two widenings
    an audited tree could attempt — cannot un-bound the lane auditing it."""

    class _HostileCfg:
        surface_role = "agent"
        tool_resolution_mode = "all"

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _HostileCfg())
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: "coding")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())
    denied = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"

    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    listed = await middleware.on_list_tools(_FakeMiddlewareContext(fastmcp_context=_FakeContext()), call_next)  # type: ignore[arg-type]
    assert {t.name for t in listed} == set(REVIEWER_TOOLS)


async def test_the_same_hostile_config_without_the_env_marker_is_an_ordinary_session(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity for the test above: without the env declaration the SAME
    config resolves as an ordinary (here, fully widened) agent session."""

    class _HostileCfg:
        surface_role = "agent"
        tool_resolution_mode = "all"

    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _HostileCfg())
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: "coding")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())

    assert await middleware.on_call_tool(ctx, _execute) is _EXECUTED  # type: ignore[arg-type]
