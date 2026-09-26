"""PRD-CORE-300-FR13 (slices S11a + S11b): no tool is hidden by run phase or task.

Phase exposure (``middleware/phase_exposure.py``) filtered the tool list by the
active run phase. A tool missing from a phase set was hidden at runtime even
when its config flag enabled it (learning L-Gzos), and ``trw_checkpoint`` was
masked in RESEARCH and PLAN (L-KZrj). S11a deleted the layer; S11b deleted the
per-task packs and the grant path, so the surface is the kernel plus every pack
whose config flag is on. This matrix is the gate: for each of the six phases,
with and without a pinned run, under each flag configuration, every masking
layer of the production chain (``_build_middleware``) is driven, and every
registered tool the flags turn on must be listed and must dispatch, while a tool
whose flag is off is denied with the flag named.

The project config deliberately still sets the retired ``phase_exposure_enabled:
true``, the setting that used to switch masking on, so a regression that
re-reads it would fail here rather than pass on a default.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from trw_mcp.models.surface_packs import FLAG_GATED_PACKS, PACK_TOOLS
from trw_mcp.models.surface_v2 import POST_CUT_KERNEL
from trw_mcp.server._surface_manifest_registry import eligible_tool_names

pytestmark = pytest.mark.integration

_PHASES = ("research", "plan", "implement", "validate", "review", "deliver")

#: Chain layers that shape handshakes or results and never mask by tool name.
#: Every OTHER layer of the production chain is composed, so a masking layer
#: added later (or phase exposure coming back) is in the matrix automatically.
_NON_MASKING_LAYERS = frozenset(
    {"BootDeferralMiddleware", "CeremonyMiddleware", "VersionDriftMiddleware", "ResponseOptimizerMiddleware"}
)
_KNOWN_MASKING_LAYERS = frozenset({"MCPSecurityMiddleware", "SurfaceAuthorityMiddleware"})

_EXECUTED = ToolResult(content=[TextContent(type="text", text="executed")])


@dataclass
class _Tool:
    name: str


@dataclass
class _Ctx:
    session_id: str
    transport: str = "stdio"


@dataclass
class _Msg:
    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class _MwCtx:
    message: Any = None
    fastmcp_context: _Ctx | None = None


#: Flag configurations under test: config.yaml body -> the flags it turns on.
_CONFIGS: dict[str, tuple[str, frozenset[str]]] = {
    "defaults": ("", frozenset({"comms_enabled"})),
    "every_flag": (
        "comms_enabled: true\nassess_enabled: true\ndispatch_tools_exposed: true\n",
        frozenset({"comms_enabled", "assess_enabled", "dispatch_tools_exposed"}),
    ),
    "comms_off": ("comms_enabled: false\n", frozenset()),
    # "all" turns on comms and assess, never dispatch (PRD-CORE-300 FR09).
    "all_mode": ("tool_resolution_mode: all\n", frozenset({"comms_enabled", "assess_enabled"})),
}


def _registered_kernel() -> frozenset[str]:
    """The kernel as the PRD states it.

    Derived from ``surface_v2.POST_CUT_KERNEL``, not from the resolver under
    test, so a kernel tool the resolver dropped cannot hide in an intersection.
    """
    return frozenset(POST_CUT_KERNEL)


def _expected(flags_on: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
    """(tools that must be listed and callable, tools that must be denied)."""
    off = frozenset(
        tool for pack, flag in FLAG_GATED_PACKS.items() if flag not in flags_on for tool in PACK_TOOLS[pack]
    )
    return frozenset(eligible_tool_names()) - off, off


@pytest.fixture(params=sorted(_CONFIGS))
def pinned_project(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Path, str, frozenset[str]]]:
    from trw_mcp.middleware.surface_authority import reset_surface_authority_state
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs
    from trw_mcp.state._pin_store import upsert_pin_entry

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    body, flags_on = _CONFIGS[request.param]
    (trw / "config.yaml").write_text("phase_exposure_enabled: true\n" + body, encoding="utf-8")
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    reset_surface_authority_state()

    run_dir = tmp_path / get_config().runs_root / "matrix" / "20260101T000000Z-matrix01"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    session_id = "sess-phase-matrix"
    upsert_pin_entry(session_id, run_dir)
    yield run_dir, session_id, flags_on

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    reset_surface_authority_state()
    _reset_config()


def _write_phase(run_dir: Path, phase: str) -> None:
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: 20260101T000000Z-matrix01\n"
        "task: matrix\n"
        "framework: v99.9_TRW\n"
        "status: active\n"
        f"phase: {phase}\n"
        "task_type: coding\n",
        encoding="utf-8",
    )


def _masking_chain() -> list[Any]:
    from trw_mcp.server._app import _build_middleware

    chain = [mw for mw in _build_middleware() if type(mw).__name__ not in _NON_MASKING_LAYERS]
    assert _KNOWN_MASKING_LAYERS <= {type(mw).__name__ for mw in chain}
    return chain


async def _list(chain: list[Any], session_id: str) -> set[str]:
    async def terminal(_ctx: Any) -> list[_Tool]:
        return [_Tool(name) for name in sorted(eligible_tool_names())]

    call_next: Any = terminal
    for mw in reversed(chain):
        call_next = (lambda m, nxt: lambda ctx: m.on_list_tools(ctx, nxt))(mw, call_next)
    return {tool.name for tool in await call_next(_MwCtx(fastmcp_context=_Ctx(session_id)))}


async def _call(chain: list[Any], session_id: str, tool_name: str) -> Any:
    async def terminal(_ctx: Any) -> ToolResult:
        return _EXECUTED

    call_next: Any = terminal
    for mw in reversed(chain):
        call_next = (lambda m, nxt: lambda ctx: m.on_call_tool(ctx, nxt))(mw, call_next)
    return await call_next(_MwCtx(message=_Msg(tool_name, {}), fastmcp_context=_Ctx(session_id)))


async def _assert_surface(chain: list[Any], session_id: str, flags_on: frozenset[str], where: str) -> None:
    must_list, must_deny = _expected(flags_on)
    assert _registered_kernel() <= must_list, "every registered kernel tool is always on"

    listed = await _list(chain, session_id)
    assert listed == must_list, f"{where}: missing={sorted(must_list - listed)} extra={sorted(listed - must_list)}"

    for tool_name in sorted(must_list):
        result = await _call(chain, session_id, tool_name)
        assert result is _EXECUTED, f"{tool_name} denied {where}: {getattr(result, 'structured_content', result)}"
    for tool_name in sorted(must_deny):
        result = await _call(chain, session_id, tool_name)
        assert result is not _EXECUTED, f"{tool_name} dispatched {where} with its flag off"
        payload = result.structured_content or {}
        assert payload.get("error_type") == "tool_not_in_surface"
        assert "enable_with" in payload, f"the denial for {tool_name} must name the flag that turns it on"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", _PHASES)
async def test_every_enabled_tool_listed_and_callable_in_every_phase(
    phase: str, pinned_project: tuple[Path, str, frozenset[str]]
) -> None:
    run_dir, session_id, flags_on = pinned_project
    _write_phase(run_dir, phase)
    await _assert_surface(_masking_chain(), session_id, flags_on, f"in phase {phase}")


@pytest.mark.asyncio
async def test_a_session_with_no_run_sees_the_same_surface(
    pinned_project: tuple[Path, str, frozenset[str]],
) -> None:
    _run_dir, _session_id, flags_on = pinned_project
    await _assert_surface(_masking_chain(), "sess-no-run", flags_on, "with no pinned run")


def test_the_kernel_includes_the_tools_bounded_surfaces_used_to_hide() -> None:
    assert {"trw_checkpoint", "trw_init", "trw_build_check", "trw_review", "trw_prd_validate"} <= _registered_kernel()


def test_the_meta_tools_are_not_registered() -> None:
    registered = set(eligible_tool_names())
    for name in ("skill_discovery", "request_tool_access", "profile_explain"):
        assert f"trw_{name}" not in registered


def test_phase_exposure_modules_are_gone() -> None:
    for module in (
        "trw_mcp.middleware.phase_exposure",
        "trw_mcp.middleware._phase_session",
        "trw_mcp.models.phase_policy",
        "trw_mcp.models.config._fields_phase_exposure",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)


def test_production_chain_has_no_phase_layer() -> None:
    from trw_mcp.server._app import _build_middleware

    names = [type(mw).__name__ for mw in _build_middleware()]
    assert not [name for name in names if "Phase" in name], names


def test_phase_exposure_enabled_is_a_retired_config_key() -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._retired_keys import retired_config_keys

    assert "phase_exposure_enabled" in retired_config_keys()
    assert "phase_exposure_enabled" not in TRWConfig.model_fields
