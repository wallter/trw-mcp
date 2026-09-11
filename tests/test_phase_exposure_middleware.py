"""PRD-INTENT-002 FR02/03/04/05/05b/07 — PhaseExposureMiddleware.

Tests assert the actual exposed-tool set per phase (resolved from a profile
fixture), the fail-open branch, the rigid-tool invariant, masked-call denial,
standard best-effort refresh, and telemetry emission.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.phase_exposure import (
    PhaseExposureMiddleware,
    resolve_active_phase,
)
from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY, RIGID_TOOLS

# ── Fakes (mirror tests/_test_middleware_ceremony_support.py) ──────────


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeRequestContext:
    session_id: str = "sess-1"


@dataclass
class _FakeContext:
    request_context: _FakeRequestContext | None = field(default_factory=_FakeRequestContext)
    _session_id: str = "sess-1"

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


def _all_default_tools() -> list[_FakeTool]:
    """Every tool named anywhere in the default policy, as Tool stubs."""
    names = set(DEFAULT_PHASE_POLICY.safe_set)
    for tools in DEFAULT_PHASE_POLICY.allowed_tools_by_phase.values():
        names.update(tools)
    return [_FakeTool(name=n) for n in sorted(names)]


@pytest.fixture
def middleware() -> PhaseExposureMiddleware:
    return PhaseExposureMiddleware(enabled=True, policy=DEFAULT_PHASE_POLICY)


# ── FR03: tool-list filtering per phase ────────────────────────────────


@pytest.mark.asyncio
async def test_research_phase_subset(middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03/US-001: RESEARCH excludes the PLAN-only requirements tools; includes
    init/recall.

    PRD-FIX-119 FR01 moved ``trw_review`` into ``RIGID_TOOLS``, so it is now
    never-hidden like ``trw_deliver``/``trw_build_check`` (it is the NO_ESCAPE
    ``review_scope_block`` remedy AND the only writer of ``Phase.REVIEW``). The
    masked-tool exemplar for this phase is therefore ``trw_prd_create``, which
    keeps the "RESEARCH really does exclude something" half of the assertion.
    """
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )
    tools = _all_default_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}
    assert "trw_init" in names
    assert "trw_recall" in names
    assert "trw_session_start" in names
    assert "trw_prd_create" not in names  # PLAN-only requirements tool, masked here
    # trw_deliver / trw_build_check / trw_review are rigid → always visible
    assert "trw_deliver" in names
    assert "trw_build_check" in names
    assert "trw_review" in names  # PRD-FIX-119 FR01/FR07


@pytest.mark.asyncio
async def test_implement_phase_subset(middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03/US-001: IMPLEMENT excludes prd_create; includes checkpoint/learn."""
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "IMPLEMENT",
    )
    tools = _all_default_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}
    assert "trw_checkpoint" in names
    assert "trw_learn" in names
    assert "trw_status" in names
    assert "trw_prd_create" not in names


@pytest.mark.asyncio
async def test_disabled_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migration: enabled=False returns the full catalogue unchanged."""
    mw = PhaseExposureMiddleware(enabled=False, policy=DEFAULT_PHASE_POLICY)
    tools = _all_default_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await mw.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert {t.name for t in out} == {t.name for t in tools}


@pytest.mark.asyncio
async def test_rigid_tools_visible_in_every_phase(
    middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: rigid tools survive filtering in all six phases."""
    tools = _all_default_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    for phase in ("RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"):
        monkeypatch.setattr(
            "trw_mcp.middleware.phase_exposure.resolve_active_phase",
            lambda phase=phase, **_: phase,
        )
        ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
        out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
        names = {t.name for t in out}
        for rigid in RIGID_TOOLS:
            assert rigid in names, f"{rigid} hidden in {phase}"


@pytest.mark.asyncio
async def test_fail_open_on_internal_error(
    middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR03/NFR02: any policy-resolution error exposes the full catalogue."""

    def _boom(**_: Any) -> str:
        raise RuntimeError("phase resolution exploded")

    monkeypatch.setattr("trw_mcp.middleware.phase_exposure.resolve_active_phase", _boom)
    tools = _all_default_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    # Full set returned — never locked away.
    assert {t.name for t in out} == {t.name for t in tools}


# ── FR05: masked tool-call denial ──────────────────────────────────────


@pytest.mark.asyncio
async def test_masked_call_denied(middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: calling a masked tool returns a tool_not_in_phase error, no body.

    Exemplar is ``trw_prd_create`` (a PLAN-bucket tool) since PRD-FIX-119 FR01
    made ``trw_review`` rigid.
    """
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )
    called = False

    async def call_next(_ctx: Any) -> Any:
        nonlocal called
        called = True
        return _FakeToolResult(content=[TextContent(type="text", text="executed")])

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(name="trw_prd_create"),
        fastmcp_context=_FakeContext(),
    )
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert called is False, "masked tool body must NOT execute"
    assert out.structured_content is not None
    assert out.structured_content["error_type"] == "tool_not_in_phase"
    assert out.structured_content["tool_name"] == "trw_prd_create"
    assert out.structured_content["current_phase"] == "RESEARCH"
    assert "trw_recall" in out.structured_content["available_tools"]


@pytest.mark.asyncio
async def test_allowed_call_executes(middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: an in-phase tool executes normally."""
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )

    async def call_next(_ctx: Any) -> Any:
        return _FakeToolResult(content=[TextContent(type="text", text="executed")])

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(name="trw_init"),
        fastmcp_context=_FakeContext(),
    )
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert out.content[0].text == "executed"


@pytest.mark.asyncio
async def test_rigid_call_never_denied(middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: a rigid tool is callable even in a phase that omits it."""
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )

    async def call_next(_ctx: Any) -> Any:
        return _FakeToolResult(content=[TextContent(type="text", text="built")])

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(name="trw_build_check"),
        fastmcp_context=_FakeContext(),
    )
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert out.content[0].text == "built"


@pytest.mark.asyncio
async def test_call_fail_open_on_error(middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05/NFR02: a resolution error during call denial fails open (executes).

    The tool must be one that would otherwise be MASKED in the resolved phase,
    or the assertion proves nothing — hence ``trw_prd_create`` rather than
    ``trw_review``, which PRD-FIX-119 FR01 made rigid (always callable).
    """

    def _boom(**_: Any) -> str:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("trw_mcp.middleware.phase_exposure.resolve_active_phase", _boom)

    async def call_next(_ctx: Any) -> Any:
        return _FakeToolResult(content=[TextContent(type="text", text="executed")])

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(name="trw_prd_create"),
        fastmcp_context=_FakeContext(),
    )
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert out.content[0].text == "executed"


@pytest.mark.asyncio
async def test_override_allows_single_masked_call(
    middleware: PhaseExposureMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR05/FR06: an active override lets one masked call through, then re-masks."""
    from trw_mcp.tools import phase_overrides

    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )
    phase_overrides.reset_overrides()
    # Exemplar must be a genuinely masked tool: PRD-FIX-119 FR01 made
    # trw_review rigid, so it can no longer demonstrate single-use unmasking.
    phase_overrides.grant_override("sess-1", "trw_prd_create", reason="x" * 25)

    calls = 0

    async def call_next(_ctx: Any) -> Any:
        nonlocal calls
        calls += 1
        return _FakeToolResult(content=[TextContent(type="text", text="executed")])

    # A FRESH context per call: FastMCP builds one MiddlewareContext per
    # tools/call. The grant is single-use per CALL (stamped on the request so
    # surface-authority and this gate can both honour one grant), so reusing a
    # single object across two calls would have the second legitimately re-read
    # the first call's own authorization.
    def _ctx() -> _FakeMiddlewareContext:
        return _FakeMiddlewareContext(
            message=_FakeMessage(name="trw_prd_create"),
            fastmcp_context=_FakeContext(),
        )

    first = await middleware.on_call_tool(_ctx(), call_next)  # type: ignore[arg-type]
    assert first.content[0].text == "executed"
    assert calls == 1
    # Second call re-masked
    second = await middleware.on_call_tool(_ctx(), call_next)  # type: ignore[arg-type]
    assert second.structured_content is not None
    assert second.structured_content["error_type"] == "tool_not_in_phase"
    assert calls == 1


# ── FR07: telemetry emission ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_telemetry_emitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07: a masked denial writes one HPOTelemetryEvent line to mask_events.jsonl."""
    run_dir = tmp_path / "task" / "run-1"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "telemetry").mkdir(parents=True)

    mw = PhaseExposureMiddleware(enabled=True, policy=DEFAULT_PHASE_POLICY)
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_run_dir_for_session",
        lambda **_: run_dir,
    )

    async def call_next(_ctx: Any) -> Any:
        return _FakeToolResult(content=[TextContent(type="text", text="x")])

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(name="trw_prd_create"),
        fastmcp_context=_FakeContext(),
    )
    await mw.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    events_file = run_dir / "telemetry" / "mask_events.jsonl"
    assert events_file.exists()
    lines = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    evt = lines[0]
    assert evt["event_type"] == "phase_exposure"
    assert evt["payload"]["event_type"] == "mask_denied"
    assert evt["payload"]["tool_name"] == "trw_prd_create"
    assert evt["payload"]["phase"] == "RESEARCH"
    # No tool arguments leaked into telemetry.
    assert "arguments" not in evt["payload"]


# ── FR02: phase resolution ─────────────────────────────────────────────


def test_phase_resolution_defaults_to_research_when_no_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: missing/absent active run defaults to RESEARCH."""
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_run_dir_for_session",
        lambda **_: None,
    )
    assert resolve_active_phase(session_id="s", fastmcp_context=None) == "RESEARCH"


def test_phase_resolution_reads_run_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: the active phase is read from run.yaml and uppercased."""
    run_dir = tmp_path / "task" / "run-1"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text("run_id: run-1\ntask: t\nphase: implement\n")
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_run_dir_for_session",
        lambda **_: run_dir,
    )
    assert resolve_active_phase(session_id="s", fastmcp_context=None) == "IMPLEMENT"


# ── PROF-001 FR-14 closure: single-source-of-truth consumption ─────────


@pytest.mark.asyncio
async def test_middleware_consumes_resolved_profile_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROF-001 FR-14: the middleware reads ResolvedProfile.allowed_tools_by_phase.

    Proves INTENT-002 consumes the PROF-001 policy surface (no parallel table):
    a profile whose RESEARCH bucket allows ONLY trw_recall masks everything else
    in RESEARCH (except the rigid + Safe Set), and that profile flows through
    ``compose`` → ``from_resolved_allowlist`` → the middleware.
    """
    from trw_mcp.models.phase_policy import from_resolved_allowlist
    from trw_mcp.profile import Profile, ProfileLayer, compose

    # Resolve a real profile through the PROF-001 chain (single source).
    resolved = compose(
        [
            ProfileLayer(
                name="defaults",
                overrides=Profile(allowed_tools_by_phase={"RESEARCH": ["trw_recall"]}),
            ),
        ]
    )
    policy = from_resolved_allowlist(resolved.profile.allowed_tools_by_phase)
    mw = PhaseExposureMiddleware(enabled=True, policy=policy)

    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )
    tools = [*_all_default_tools(), _FakeTool(name="trw_recall")]

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await mw.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}
    # The profile's RESEARCH bucket only listed trw_recall → trw_init masked.
    assert "trw_recall" in names
    assert "trw_init" not in names
    # Rigid tools still visible (never-hide invariant survives the profile).
    for rigid in RIGID_TOOLS:
        assert rigid in names


def test_phase_resolution_unreadable_run_yaml_defaults_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR02 / Failure Mode: an unreadable run.yaml defaults to RESEARCH."""
    run_dir = tmp_path / "task" / "run-1"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text(": : : not valid yaml : :\n\t- broken")
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_run_dir_for_session",
        lambda **_: run_dir,
    )
    assert resolve_active_phase(session_id="s", fastmcp_context=None) == "RESEARCH"


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_error", [False, True])
async def test_allowed_call_observes_changed_phase_without_redispatch(monkeypatch, handler_error):
    from trw_mcp.middleware import _phase_transitions as pt

    pt.reset_transition_state()
    pt.detect_transition("sess-1", "RESEARCH")
    phase = "RESEARCH"
    emitted = []
    calls = 0
    failure = RuntimeError("handler failed after changing phase")
    result = _FakeToolResult()

    async def emit(ctx):
        emitted.append(phase)
        return True

    async def call_next(ctx):
        nonlocal phase, calls
        calls += 1
        phase = "IMPLEMENT"
        if handler_error:
            raise failure
        return result

    monkeypatch.setattr("trw_mcp.middleware.phase_exposure.resolve_active_phase", lambda **_: phase)
    monkeypatch.setattr("trw_mcp.middleware.phase_exposure.emit_list_changed", emit)
    mw = PhaseExposureMiddleware(enabled=True, policy=DEFAULT_PHASE_POLICY)
    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_recall"), fastmcp_context=_FakeContext())
    if handler_error:
        with pytest.raises(RuntimeError) as caught:
            await mw.on_call_tool(ctx, call_next)
        assert caught.value is failure
    else:
        assert await mw.on_call_tool(ctx, call_next) is result
    assert calls == 1
    assert emitted == ["IMPLEMENT"]
