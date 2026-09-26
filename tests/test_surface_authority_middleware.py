"""PRD-CORE-218 FR03/FR04, flattened by PRD-CORE-300 S11b — SurfaceAuthorityMiddleware
acceptance tests.

Enters through the middleware's real ``on_list_tools`` / ``on_call_tool`` hooks
(the production entrypoint the activation wires into the chain). Proves: standard
mode masks a flag-gated tool whose flag is off; a resolution failure fails OPEN;
a denial payload names the config flag that turns the tool on; and a surface
change (a config flag flip) still notifies on the LIST path. The surface no
longer depends on the task or the run phase — there is no grant path and no
per-task pack resolution (both deleted in S11b), so this file no longer forces
``task_type`` or plants a grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from trw_mcp.middleware.surface_authority import (
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS, KERNEL_TOOLS, PACK_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names, resolve_tool_surface

_MOD = "trw_mcp.middleware.surface_authority"


# ── Fakes ──────────────────────────────────────────────────────────────


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeContext:
    _session_id: str = "sess-1"

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


_SENTINEL = object()


def _all_tools() -> list[_FakeTool]:
    return [_FakeTool(name=n) for n in sorted(eligible_tool_names())]


@pytest.fixture
def middleware() -> SurfaceAuthorityMiddleware:
    return SurfaceAuthorityMiddleware()


@pytest.fixture(autouse=True)
def _clear_state() -> Any:
    reset_surface_authority_state()
    yield
    reset_surface_authority_state()


class _StubConfig:
    """Only the attributes ``_resolve`` reads off the config."""

    def __init__(self, *, comms_enabled: bool = False, dispatch_enabled: bool = False, assess_enabled: bool = False):
        self.comms_enabled = comms_enabled
        self.dispatch_tools_exposed = dispatch_enabled
        self.assess_enabled = assess_enabled


def _force(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
    comms_enabled: bool = False,
    dispatch_enabled: bool = False,
    assess_enabled: bool = False,
) -> None:
    monkeypatch.setattr(f"{_MOD}._resolve_mode", lambda: mode)
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: _StubConfig(
            comms_enabled=comms_enabled, dispatch_enabled=dispatch_enabled, assess_enabled=assess_enabled
        ),
    )


# ── FR04: standard default, every flag off → the always-on surface only ─


@pytest.mark.asyncio
async def test_standard_all_flags_off_masks_flag_gated_packs(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standard mode with every pack flag off exposes exactly ALWAYS_ON_TOOLS;
    a flag-gated pack tool (trw_dispatch) is masked."""
    _force(monkeypatch, mode="standard")

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}

    assert names == ALWAYS_ON_TOOLS & set(eligible_tool_names())
    assert "trw_dispatch" not in names  # dispatch pack is gated by dispatch_tools_exposed
    assert "trw_inbox" not in names  # peer_comms pack is gated by comms_enabled
    assert "trw_assess" not in names  # assess_support pack is gated by assess_enabled
    assert "trw_session_start" in names  # kernel
    assert "trw_code" in names  # kernel since S10/S11b
    assert "trw_init" in names  # kernel


# ── FR04/S11b: a flag turns its pack on ─────────────────────────────────


@pytest.mark.asyncio
async def test_comms_flag_on_exposes_peer_comms_pack(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning comms_enabled on adds exactly the peer_comms pack."""
    _force(monkeypatch, mode="standard", comms_enabled=True)

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}

    expected = (ALWAYS_ON_TOOLS | set(PACK_TOOLS["peer_comms"])) & set(eligible_tool_names())
    assert names == expected
    assert "trw_dispatch" not in names  # dispatch still needs its own flag


# ── FR04/FR09: explicit all turns on comms + assess but never dispatch ──


@pytest.mark.asyncio
async def test_mode_all_exposes_comms_and_assess_but_not_dispatch(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode='all' widens comms + assess (PRD-CORE-300 FR09) but still needs
    dispatch_tools_exposed for the dispatch pack — process launching is never
    on by default in any mode."""
    _force(monkeypatch, mode="all")
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}

    assert "trw_inbox" in names  # comms widened by all mode
    assert "trw_assess" in names  # assess widened by all mode
    assert "trw_dispatch" not in names  # dispatch is NOT widened by all mode alone


@pytest.mark.asyncio
async def test_mode_all_with_dispatch_flag_allows_the_dispatch_call(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force(monkeypatch, mode="all", dispatch_enabled=True)

    async def call_next(_ctx: Any) -> Any:
        return _SENTINEL

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_dispatch"), fastmcp_context=_FakeContext())
    assert await middleware.on_call_tool(ctx, call_next) is _SENTINEL  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mode_all_without_dispatch_flag_still_denies_dispatch_call(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force(monkeypatch, mode="all")

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("masked call must not reach the tool")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_dispatch"), fastmcp_context=_FakeContext())
    denied = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert denied.structured_content["error_type"] == "tool_not_in_surface"


# ── Masked call denied, naming the gating flag ──────────────────────────


@pytest.mark.asyncio
async def test_masked_call_denied_names_the_gating_flag(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flag-gated tool outside the resolved surface is denied with a payload
    naming the config flag that turns it on (no packs/override_hint keys)."""
    _force(monkeypatch, mode="standard")

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("masked call must not reach the tool")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_dispatch"), fastmcp_context=_FakeContext())
    result = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    payload = result.structured_content
    assert payload is not None
    assert payload["error_type"] == "tool_not_in_surface"
    assert payload["tool_name"] == "trw_dispatch"
    assert payload["enable_with"] == "dispatch_tools_exposed: true in .trw/config.yaml"
    assert "packs" not in payload
    assert "override_hint" not in payload
    assert "dispatch_tools_exposed" in result.content[0].text


@pytest.mark.asyncio
async def test_masked_call_denied_no_flag_names_no_enable_with(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool outside the surface for a reason other than a flag (there is none
    left in standard mode, since every non-flag pack is always on) is a
    structurally impossible case today; this asserts the denial shape is still
    honest when no flag applies — covered via an unregistered/unknown name."""
    _force(monkeypatch, mode="standard")

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("must not reach the tool")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_nonexistent_tool"), fastmcp_context=_FakeContext())
    result = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    payload = result.structured_content
    assert payload["error_type"] == "tool_not_in_surface"
    assert "enable_with" not in payload


# ── Diagnostic finding: a raising tool must execute exactly once ────────


@pytest.mark.asyncio
async def test_raising_tool_call_next_invoked_exactly_once(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool that raises inside ``call_next`` must be invoked exactly once.

    Regression for the double-invocation bug: ``on_call_tool`` used to call
    ``call_next`` inside ``try`` AND again in the fail-open ``except`` when the
    tool's own exception propagated up, so a raising tool ran twice. A kernel
    tool is used so the call reaches the tool (in-surface) rather than being
    denied.
    """
    _force(monkeypatch, mode="standard")
    calls = {"n": 0}

    async def call_next(_ctx: Any) -> Any:
        calls["n"] += 1
        raise RuntimeError("tool exploded")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_session_start"), fastmcp_context=_FakeContext())
    with pytest.raises(RuntimeError, match="tool exploded"):
        await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert calls["n"] == 1


# ── NFR02: resolution failure fails OPEN ────────────────────────────────


@pytest.mark.asyncio
async def test_resolution_failure_fails_open_list(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken resolver exposes the FULL catalogue (never bricks a session)."""

    def _boom() -> str:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(f"{_MOD}._resolve_mode", _boom)
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    with capture_logs() as logs:
        out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert {t.name for t in out} == {t.name for t in tools}
    assert any(e.get("outcome") == "fail_open" for e in logs)


@pytest.mark.asyncio
async def test_resolution_failure_fails_open_call(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken resolver executes the call rather than wrongly blocking it."""

    def _boom() -> str:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(f"{_MOD}._resolve_mode", _boom)

    async def call_next(_ctx: Any) -> Any:
        return _SENTINEL

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_dispatch"), fastmcp_context=_FakeContext())
    assert await middleware.on_call_tool(ctx, call_next) is _SENTINEL  # type: ignore[arg-type]


# ── F1a: the middleware is actually in the production chain ──────────────


def test_middleware_registered_in_build_middleware() -> None:
    """Production-path proof: _build_middleware() installs SurfaceAuthorityMiddleware.
    Round-1 audit F1: nothing asserted chain membership."""
    from trw_mcp.server._app import _build_middleware

    types = [type(m).__name__ for m in _build_middleware()]
    assert "SurfaceAuthorityMiddleware" in types, types


# ── P2d: denials are observable ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_denial_emits_structured_event(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every masked call logs a structured warning naming tool/mode/flag."""
    _force(monkeypatch, mode="standard")

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("must not reach tool")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_dispatch"), fastmcp_context=_FakeContext())
    with capture_logs() as logs:
        await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    denied = [e for e in logs if e.get("event") == "surface_authority_call_denied"]
    assert denied, logs
    ev = denied[0]
    assert ev["tool"] == "trw_dispatch"
    assert ev["mode"] == "standard"
    assert ev["flag"] == "dispatch_tools_exposed"


# ── P2a: a surface change notifies capable clients ──────────────────────


@dataclass
class _RecordingSession:
    calls: int = 0

    async def send_tool_list_changed(self) -> None:
        self.calls += 1


@dataclass
class _CtxWithSession:
    _session_id: str
    session: Any = None

    @property
    def session_id(self) -> str:
        return self._session_id


@pytest.mark.asyncio
async def test_surface_change_emits_list_changed(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a session's resolved surface changes (a config reload flipped a
    flag), the LIST path emits notifications/tools/list_changed; the first
    listing seeds silently."""
    flags = {"comms_enabled": False}
    monkeypatch.setattr(f"{_MOD}._resolve_mode", lambda: "standard")
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: _StubConfig(comms_enabled=flags["comms_enabled"]),
    )

    session = _RecordingSession()
    ctx = _FakeMiddlewareContext(fastmcp_context=_CtxWithSession("sess-1", session))

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    # First listing seeds the ledger, no notify.
    await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert session.calls == 0
    # comms_enabled flips → surface changes → notify emitted.
    flags["comms_enabled"] = True
    await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert session.calls == 1
    # Re-listing with the SAME surface does not re-notify.
    await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert session.calls == 1


# ── F1b + P2b: real-chain entrypoint (no monkeypatch of the resolution seam) ──


@pytest.mark.integration
async def test_real_chain_entrypoint_masks_and_denies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Production-path proof (F1b): drive the REAL SurfaceAuthorityMiddleware with
    real config (TRW_PROJECT_ROOT tmp project) — no monkeypatch of ``_resolve``,
    ``_resolve_mode`` or ``get_config``. Standard mode, real defaults
    (comms_enabled=True, dispatch_tools_exposed=False)."""
    from trw_mcp.models.config import _reset_config, get_config

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    _reset_config()

    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    (trw / "config.yaml").write_text("tool_resolution_mode: standard\n", encoding="utf-8")
    _reset_config()

    config = get_config()
    assert config.comms_enabled is True
    assert config.dispatch_tools_exposed is False

    mw = SurfaceAuthorityMiddleware()  # REAL — no seam monkeypatching
    session_id = "sess-real"
    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext(session_id))

    async def call_next_list(_ctx: Any) -> Any:
        return _all_tools()

    listed = {t.name for t in await mw.on_list_tools(ctx, call_next_list)}  # type: ignore[arg-type]
    expected = (ALWAYS_ON_TOOLS | set(PACK_TOOLS["peer_comms"])) & set(eligible_tool_names())
    assert listed == expected
    assert "trw_inbox" in listed  # comms default-on
    assert "trw_dispatch" not in listed  # dispatch pack off by default

    async def call_next_deny(_ctx: Any) -> Any:
        raise AssertionError("masked call must not reach the tool")

    deny_ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_dispatch"), fastmcp_context=_FakeContext(session_id))
    denied = await mw.on_call_tool(deny_ctx, call_next_deny)  # type: ignore[arg-type]
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"

    _reset_config()


def test_resolve_tool_surface_agrees_with_kernel_membership() -> None:
    """The registry's kernel-derived tools stay a subset of KERNEL_TOOLS."""
    standard = resolve_tool_surface("standard")
    assert set(KERNEL_TOOLS) & set(eligible_tool_names()) <= set(standard.tools)
