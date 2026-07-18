"""PRD-CORE-218 FR03/FR04 — SurfaceAuthorityMiddleware acceptance tests.

Enters through the middleware's real ``on_list_tools`` / ``on_call_tool`` hooks
(the production entrypoint the activation wires into the chain), mirroring the
``test_phase_exposure_middleware.py`` idiom. Proves: standard mode masks a
non-kernel tool for a session with no run; a run with ``task_type=coding``
exposes kernel + the coding packs; ``mode="all"`` exposes everything (operator
escape); a ``trw_request_tool_access`` grant unmasks a masked pack tool for
exactly one call; a resolution failure fails OPEN; and the denial payload names
the containing pack + ``trw_request_tool_access``.
"""

from __future__ import annotations

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
from trw_mcp.models.surface_packs import KERNEL_TOOLS, PACK_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names, resolve_tool_surface
from trw_mcp.tools import phase_overrides

_MOD = "trw_mcp.middleware.surface_authority"


# ── Fakes (mirror test_phase_exposure_middleware.py) ────────────────────


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
def _clear_overrides() -> Any:
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    yield
    phase_overrides.reset_overrides()
    reset_surface_authority_state()


def _force(monkeypatch: pytest.MonkeyPatch, *, mode: str, task_type: str | None) -> None:
    monkeypatch.setattr(f"{_MOD}._resolve_mode", lambda: mode)
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: task_type)


# ── FR04: standard default, no run → kernel only ────────────────────────


@pytest.mark.asyncio
async def test_standard_no_run_masks_non_kernel(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No active run under standard mode → only kernel ∪ rigid survive; a
    non-kernel pack tool (trw_code_search) is masked."""
    _force(monkeypatch, mode="standard", task_type=None)

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}

    assert names == (set(KERNEL_TOOLS) | _ALWAYS_EXPOSED) & set(eligible_tool_names())
    assert "trw_code_search" not in names  # code_navigation pack is masked
    assert "trw_session_start" in names  # kernel
    assert "trw_build_check" in names  # rigid (NOT kernel) — never locked out
    assert "trw_init" in names  # bootstrap-critical (P2b): fresh session must init


# ── FR03: task-selected packs ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_coding_run_exposes_coding_packs(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with task_type=coding exposes kernel + verification + code_navigation."""
    _force(monkeypatch, mode="standard", task_type="coding")

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    names = {t.name for t in out}

    expected = (set(resolve_tool_surface("coding", "standard").tools) | _ALWAYS_EXPOSED) & set(eligible_tool_names())
    assert names == expected
    assert "trw_code_search" in names  # code_navigation pack (coding)
    assert "trw_build_check" in names  # verification pack
    assert "trw_entity_risk_map" not in names  # code_risk pack NOT in coding standard


# ── FR04: explicit all is a strict no-op (operator escape) ──────────────


@pytest.mark.asyncio
async def test_mode_all_exposes_everything(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode='all' passes the full catalogue through unchanged (no masking)."""
    _force(monkeypatch, mode="all", task_type="coding")
    tools = _all_tools()

    async def call_next(_ctx: Any) -> Any:
        return tools

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    out = await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert {t.name for t in out} == {t.name for t in tools}


@pytest.mark.asyncio
async def test_mode_all_allows_any_call(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force(monkeypatch, mode="all", task_type=None)

    async def call_next(_ctx: Any) -> Any:
        return _SENTINEL

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_code_search"), fastmcp_context=_FakeContext())
    assert await middleware.on_call_tool(ctx, call_next) is _SENTINEL  # type: ignore[arg-type]


# ── FR03: masked call denied + FR06 discoverability payload ─────────────


@pytest.mark.asyncio
async def test_masked_call_denied_with_pack_and_request_access(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A masked pack tool is denied with a payload naming the pack(s) and
    trw_request_tool_access (the discoverability contract)."""
    _force(monkeypatch, mode="standard", task_type=None)

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("masked call must not reach the tool")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_code_search"), fastmcp_context=_FakeContext())
    result = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    payload = result.structured_content
    assert payload is not None
    assert payload["error_type"] == "tool_not_in_surface"
    assert payload["tool_name"] == "trw_code_search"
    assert "code_navigation" in payload["packs"]
    assert payload["packs"] == [p for p, tools in PACK_TOOLS.items() if "trw_code_search" in tools]
    assert "trw_request_tool_access" in payload["override_hint"]
    assert "trw_request_tool_access" in result.content[0].text


# ── FR06: request_tool_access grant unmasks one call (the verified path) ─


@pytest.mark.asyncio
async def test_grant_unmasks_for_exactly_one_call(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single-use trw_request_tool_access grant unmasks a masked pack tool in
    both the LIST view and ONE call; the second call re-masks (single-use)."""
    _force(monkeypatch, mode="standard", task_type=None)
    phase_overrides.grant_override("sess-1", "trw_code_search", reason="need a one-off code search for audit")

    # LIST view: the grant surfaces the tool.
    async def call_next_list(_ctx: Any) -> Any:
        return _all_tools()

    list_ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    listed = {t.name for t in await middleware.on_list_tools(list_ctx, call_next_list)}  # type: ignore[arg-type]
    assert "trw_code_search" in listed

    calls = {"n": 0}

    async def call_next_call(_ctx: Any) -> Any:
        calls["n"] += 1
        return _SENTINEL

    call_ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_code_search"), fastmcp_context=_FakeContext())
    # First call: grant consumed → reaches the tool.
    assert await middleware.on_call_tool(call_ctx, call_next_call) is _SENTINEL  # type: ignore[arg-type]
    assert calls["n"] == 1
    # Second call: grant already consumed → denied (masked again).
    denied = await middleware.on_call_tool(call_ctx, call_next_call)  # type: ignore[arg-type]
    assert calls["n"] == 1
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"


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

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_code_search"), fastmcp_context=_FakeContext())
    assert await middleware.on_call_tool(ctx, call_next) is _SENTINEL  # type: ignore[arg-type]


# ── F1a: the middleware is actually in the production chain ──────────────


def test_middleware_registered_in_build_middleware_before_phase_exposure() -> None:
    """Production-path proof: _build_middleware() installs SurfaceAuthorityMiddleware
    and places it BEFORE PhaseExposureMiddleware (so phase masking composes within
    the CORE-218 surface). Round-1 audit F1: nothing asserted chain membership."""
    from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware  # noqa: F401
    from trw_mcp.server._app import _build_middleware

    chain = _build_middleware()
    types = [type(m).__name__ for m in chain]
    assert "SurfaceAuthorityMiddleware" in types, types
    assert "PhaseExposureMiddleware" in types, types
    assert types.index("SurfaceAuthorityMiddleware") < types.index("PhaseExposureMiddleware")


# ── P2d: denials are observable ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_denial_emits_structured_event(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every masked call logs a structured warning naming tool/task_type/mode/packs."""
    _force(monkeypatch, mode="standard", task_type="rca")

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("must not reach tool")

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_prd_create"), fastmcp_context=_FakeContext())
    with capture_logs() as logs:
        await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    denied = [e for e in logs if e.get("event") == "surface_authority_call_denied"]
    assert denied, logs
    ev = denied[0]
    assert ev["tool"] == "trw_prd_create"
    assert ev["task_type"] == "rca"
    assert ev["mode"] == "standard"
    assert "requirements" in ev["packs"]


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
    """When a session's resolved surface changes (task_type shift), the LIST path
    emits notifications/tools/list_changed; the first listing seeds silently."""
    task = {"t": "unknown"}
    monkeypatch.setattr(f"{_MOD}._resolve_mode", lambda: "standard")
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: task["t"])

    session = _RecordingSession()
    ctx = _FakeMiddlewareContext(fastmcp_context=_CtxWithSession("sess-1", session))

    async def call_next(_ctx: Any) -> Any:
        return _all_tools()

    # First listing (unknown → kernel) seeds the ledger, no notify.
    await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert session.calls == 0
    # task_type shifts to coding → surface changes → notify emitted.
    task["t"] = "coding"
    await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert session.calls == 1
    # Re-listing with the SAME surface does not re-notify.
    await middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    assert session.calls == 1


# ── F1b + P2b: real-chain entrypoint (no monkeypatch of the two seams) ───


@pytest.mark.integration
async def test_real_chain_entrypoint_masks_denies_grants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Production-path proof (F1b): drive the REAL SurfaceAuthorityMiddleware with
    tool_resolution_mode from real config (TRW_PROJECT_ROOT tmp project) and a
    task_type resolved GENUINELY from a real pin + run.yaml — no monkeypatch of
    _resolve_mode or resolve_task_type. Covers the newly-mapped 'rca' type (F2),
    the bootstrap trw_init exposure (P2b), a real denial, and a real grant."""
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs
    from trw_mcp.state._pin_store import upsert_pin_entry

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()

    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    # Real config: standard is the default; write it explicitly for the record.
    (trw / "config.yaml").write_text("tool_resolution_mode: standard\n", encoding="utf-8")
    _reset_config()

    config = get_config()
    run_dir = tmp_path / config.runs_root / "rca-task" / "20260101T000000Z-rca00001"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: 20260101T000000Z-rca00001\n"
        "task: rca-task\n"
        "framework: v26.1_TRW\n"
        "status: active\n"
        "phase: implement\n"
        "task_type: rca\n",
        encoding="utf-8",
    )
    session_id = "sess-real-rca"
    upsert_pin_entry(session_id, run_dir)

    mw = SurfaceAuthorityMiddleware()  # REAL — no seam monkeypatching
    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext(session_id))

    async def call_next_list(_ctx: Any) -> Any:
        return _all_tools()

    listed = {t.name for t in await mw.on_list_tools(ctx, call_next_list)}  # type: ignore[arg-type]
    rca_surface = (set(resolve_tool_surface("rca", "standard").tools) | _ALWAYS_EXPOSED) & set(eligible_tool_names())
    assert listed == rca_surface
    assert "trw_code_search" in listed  # code_navigation (rca) resolved via real pin+run.yaml
    assert "trw_build_check" in listed  # verification (rca)
    assert "trw_init" in listed  # bootstrap-critical (P2b)
    assert "trw_prd_create" not in listed  # requirements pack NOT in rca
    assert "trw_entity_risk_map" not in listed  # code_risk pack NOT in rca

    # Real denial through the call path.
    async def call_next_deny(_ctx: Any) -> Any:
        raise AssertionError("masked call must not reach the tool")

    deny_ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_prd_create"), fastmcp_context=_FakeContext(session_id))
    denied = await mw.on_call_tool(deny_ctx, call_next_deny)  # type: ignore[arg-type]
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_surface"

    # Real grant unmasks exactly one call.
    phase_overrides.grant_override(session_id, "trw_prd_create", reason="need a one-off prd_create for rca")

    async def call_next_allow(_ctx: Any) -> Any:
        return _SENTINEL

    allow_ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_prd_create"), fastmcp_context=_FakeContext(session_id))
    assert await mw.on_call_tool(allow_ctx, call_next_allow) is _SENTINEL  # type: ignore[arg-type]
