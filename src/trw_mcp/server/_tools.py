"""Tool, resource, and prompt registration for the TRW MCP server.

All tools, resources, and prompts are registered eagerly at import
so they are available via ``fastmcp run`` and test imports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, TypeVar

import structlog

from trw_mcp.server._app import mcp
from trw_mcp.server._boot_timeline import emit_boot_phase

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = structlog.get_logger(__name__)
_AsyncResultT = TypeVar("_AsyncResultT")

#: A tool registrar binds a group of tools onto a FastMCP server instance.
ToolRegistrar = Callable[["FastMCP"], None]


def _run_async(coro: Coroutine[object, object, _AsyncResultT]) -> _AsyncResultT:
    """Run an async coroutine from sync startup code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _tool_registrars() -> tuple[ToolRegistrar, ...]:
    """Return the ordered tuple of tool-registrar callables.

    Single source of truth for the raw tool surface (PRD-CORE-218 FR01). Both
    ``_register_tools`` (production boot) and ``raw_registered_tool_names``
    (manifest-parity probe) iterate THIS tuple, so the registered surface and
    the parity fixture can never drift apart. Registration order is not
    significant to tool availability.
    """
    from trw_mcp.tools.assess import register_assess_tools
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.ceremony_feedback import register_ceremony_feedback_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.code import register_code_tools
    from trw_mcp.tools.dispatch import register_dispatch_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.review import register_review_tools
    from trw_mcp.tools.swarm_comms import register_swarm_comms_tools

    return (
        register_build_tools,
        register_ceremony_tools,
        # PRD-CORE-069-FR06/FR08 (FIX-051): human-in-the-loop ceremony
        # de-escalation — operator status / approve / revert kill-switch tools.
        register_ceremony_feedback_tools,
        register_checkpoint_tools,
        register_learning_tools,
        register_orchestration_tools,
        # PRD-CORE-274 slice 1: cross-harness peer presence. Registered
        # unconditionally; execution is gated by comms_enabled=false.
        register_swarm_comms_tools,
        # trw-jev slice 1: opt-in decision seam. Registered unconditionally;
        # execution is gated by assess_enabled=false (PRD-CORE-288).
        register_assess_tools,
        register_requirements_tools,
        register_review_tools,
        # PRD-CORE-300-FR12: trw_code — code search, symbol lookup and
        # before-edit hints in one tool, registered in every install.
        register_code_tools,
        # Sibling MCP tools moved to `trw-mcp telemetry channel-stats` and
        # `pipeline-health` (PRD-CORE-300 slices S3a, S3b).
        # Cross-client dispatch Phase 3: dispatch launcher MCP tools.
        register_dispatch_tools,
        # PRD-CORE-208 FR04/FR05: read-only delivery status + guarded recovery.
    )


def raw_registered_tool_names() -> frozenset[str]:
    """Return every tool name the registrars produce (the full registered surface).

    This is the authoritative *raw public surface* (PRD-CORE-218 FR01). The
    production exposure authority (``SurfaceAuthorityMiddleware``, driven by
    ``tool_resolution_mode``) is a per-session MASK applied at dispatch — it never
    deregisters a tool, so this raw surface is stable regardless of the resolved
    mode. Registers on a throwaway server so it never mutates the live ``mcp`` app.
    """
    from fastmcp import FastMCP

    probe = FastMCP("trw-surface-parity-probe")
    for registrar in _tool_registrars():
        registrar(probe)
    return frozenset(t.name for t in _run_async(probe.list_tools()))


def live_registered_tool_names(app: FastMCP) -> frozenset[str] | None:
    """Return the RAW registered tool names on *app*, or None when unobtainable.

    Uses ``FastMCP._list_tools`` rather than the public ``list_tools``: the
    public method runs the middleware chain, and ``SurfaceAuthorityMiddleware``
    is a per-session exposure MASK (13 of 48 tools on the default resolution
    mode). Parity is a statement about the *registered* surface, so masking the
    answer would report drift for every tool the current session cannot see.

    Returns ``None`` — never a silently empty set — when the private accessor is
    absent, so the caller can say the check did not run instead of reporting a
    clean comparison it never made.
    """
    lister = getattr(app, "_list_tools", None)
    if not callable(lister):
        return None
    return frozenset(t.name for t in _run_async(lister()))


def _assert_manifest_parity(app: FastMCP) -> None:
    """PRD-CORE-218 FR01: log any drift between the manifest and registration.

    Reads the registered names off the LIVE *app* (PRD-CORE-248 FR03). It used
    to call ``raw_registered_tool_names()``, which builds a second, throwaway
    ``FastMCP`` and re-runs every registrar against it — a full duplicate pass
    of Pydantic schema generation for the whole tool surface, on every process
    start of every client, purely to emit an advisory warning. By the time this
    runs the live app is already fully registered, so the names are right there.
    Measured saving: 0.066 s of a 1.106 s cold import (about 6 %) — worth
    removing because it is pure waste, not because it closes a latency gap.

    ``raw_registered_tool_names()`` is retained: it is the throwaway-probe
    surface the acceptance tests use, and its own docstring names that test as
    the authority. Only the boot-time call changes.

    The authoritative bidirectional parity assertion lives in the FR01
    acceptance test. At boot we only emit a visible WARNING on drift and never
    raise — a manifest bookkeeping lapse must not brick server startup.
    """
    try:
        from trw_mcp.server._surface_manifest_registry import MANIFEST_BY_NAME

        registered = live_registered_tool_names(app)
        if registered is None:
            logger.warning(
                "surface_manifest_parity_unavailable",
                reason="the FastMCP raw tool accessor is absent; parity was NOT checked",
            )
            return
        manifest = set(MANIFEST_BY_NAME)
        missing = registered - manifest
        orphan = manifest - registered
        if missing or orphan:
            logger.warning(
                "surface_manifest_parity_drift",
                unmanifested_tools=sorted(missing),
                orphan_manifest_entries=sorted(orphan),
            )
    except Exception:  # justified: parity check is advisory; never block boot
        logger.debug("surface_manifest_parity_check_failed", exc_info=True)


def _register_tools() -> None:
    """Register all tools, resources, and prompts on the MCP server."""
    from trw_mcp.prompts.aaref import register_aaref_prompts
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources

    for registrar in _tool_registrars():
        registrar(mcp)

    register_config_resources(mcp)
    register_run_state_resources(mcp)
    register_template_resources(mcp)

    register_aaref_prompts(mcp)

    # PRD-CORE-218 FR01: verify the registered surface matches the authoritative
    # manifest (advisory at boot; hard assertion in the acceptance test).
    # PRD-CORE-248 FR03: read off the live app — boot builds ONE FastMCP and
    # runs each registrar exactly once.
    _assert_manifest_parity(mcp)

    # Mark the ceremony floor always-loaded so a deferring client (Claude Code
    # defers every MCP schema by default) does not make the agent pay a
    # ToolSearch round-trip before it can call trw_session_start. Must run AFTER
    # registration: it mutates the registered tool singletons.
    _apply_always_load_meta()

    # PRD-CORE-218 FR03/FR04: the production tool-exposure authority is now the
    # kernel/pack resolver enforced by SurfaceAuthorityMiddleware (masking at the
    # middleware layer so pack tools stay registered + grantable). The former
    # PRD-CORE-125 boot-time preset filter (_apply_tool_exposure_filter) is
    # removed — no dormant second authority.

    # PRD-INFRA-164-FR07: freeze the live-process fingerprint AFTER registration
    # so it binds the realized public surface. Fail-safe:
    # a construction failure leaves the fingerprint UNSET (currentness=unknown),
    # never blocking boot.
    from trw_mcp.server._live_fingerprint import freeze_live_process_fingerprint

    freeze_live_process_fingerprint(mcp)

    # PRD-INFRA-SEC-001 FR-9 (sprint-96 carry-forward a): wire
    # consult_mcp_security into per-tool dispatch. FastMCP's tool-manager
    # internals (``_tools`` / ``_tool_manager._tools``) vary across
    # releases, so the rewrap here is best-effort: it covers every tool whose
    # underlying callable we can resolve and silently skips if the FastMCP
    # version does not expose a rewrap point. (The sprint-96 tools that also
    # consulted from their own bodies became CLI verbs in PRD-CORE-300.)
    _apply_security_consult_wrapping()


def _apply_always_load_meta() -> None:
    """Apply the deferral opt-out to the always-on kernel (fail-open at boot).

    See ``server/_always_load.py`` for which tools qualify: the kernel, plus each
    flag-gated tool whose config flag is on. Failure here costs a ToolSearch
    round-trip, never a boot.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.server._always_load import GATING_FLAGS, apply_always_load_meta

        config = get_config()
        flags = {flag: bool(getattr(config, flag, False)) for flag in GATING_FLAGS}
        applied = _run_async(apply_always_load_meta(mcp, flags=flags))
        logger.debug("always_load_meta_applied", tools=list(applied))
    except Exception:  # justified: fail-open, deferral metadata is an optimization
        logger.info("always_load_meta_failed", reason="deferral opt-out not applied")


def _apply_security_consult_wrapping() -> None:
    """Best-effort rewrap of registered tools with security_consult.

    FastMCP exposes no public rewrap API and its private tool-manager
    attributes changed across releases. We probe a small set of known
    attribute paths; if none resolve, we log at debug and return. The
    inner-body consult inside each sprint-96 tool body remains the
    authoritative coverage.
    """
    try:
        from trw_mcp.server._security_hook import consult_mcp_security
        from trw_mcp.telemetry.tool_call_timing import wrap_tool

        tools: dict[str, object] = {}

        tool_manager = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
        if tool_manager is not None:
            raw_tools = getattr(tool_manager, "_tools", None) or getattr(tool_manager, "tools", None)
            if isinstance(raw_tools, dict):
                tools.update({str(name): tool_obj for name, tool_obj in raw_tools.items()})

        local_provider = getattr(mcp, "_local_provider", None)
        if local_provider is not None:
            components = getattr(local_provider, "_components", None)
            if isinstance(components, dict):
                for key, component in components.items():
                    if isinstance(key, str) and key.startswith("tool:"):
                        name = key.split("tool:", 1)[1].split("@", 1)[0]
                        tools[name] = component

        if not tools:
            logger.debug("security_consult_rewrap_skipped", reason="no_tools_mapping")
            return
        rewrapped = 0
        for name, tool_obj in list(tools.items()):
            fn = getattr(tool_obj, "fn", None) or getattr(tool_obj, "func", None)
            if not callable(fn) or getattr(fn, "__trw_tool_call_wrapped__", False):
                continue
            wrapped = wrap_tool(fn, tool_name=str(name), security_consult=consult_mcp_security)
            if hasattr(tool_obj, "fn"):
                tool_obj.fn = wrapped
                rewrapped += 1
            elif hasattr(tool_obj, "func"):
                tool_obj.func = wrapped
                rewrapped += 1
        logger.debug("security_consult_rewrap_applied", count=rewrapped)
    except Exception:  # justified: fail-open, rewrap is a best-effort enhancement
        logger.debug("security_consult_rewrap_failed", exc_info=True)


# ---------------------------------------------------------------------------
# PRD-CORE-218 FR06 seam: the generated-instructions renderer consumes the
# FR01 manifest through these two exports (see bootstrap/_client_integrations
# ``resolved_profile_from_manifest_seam``). capability_class is the flag view
# (PRD-CORE-300 S11b): a tool in a flag-gated pack is "gated" (on only when its
# config flag is), every other registered tool is "available".
# ---------------------------------------------------------------------------


def _static_capability_class(pack: str) -> str:
    from trw_mcp.models.surface_packs import FLAG_GATED_PACKS

    return "gated" if pack in FLAG_GATED_PACKS else "available"


def _build_surface_manifest_export() -> tuple[dict[str, str], ...]:
    from trw_mcp.server._surface_manifest_registry import TOOL_MANIFEST

    return tuple(
        {
            "tool_id": entry.name,
            "pack": entry.pack,
            "capability_class": _static_capability_class(entry.pack),
            "lifecycle": str(entry.lifecycle.value if hasattr(entry.lifecycle, "value") else entry.lifecycle),
        }
        for entry in TOOL_MANIFEST
    )


def _kernel_tools_export() -> tuple[str, ...]:
    from trw_mcp.server._surface_manifest_registry import _KERNEL_TOOLS

    return _KERNEL_TOOLS


KERNEL_TOOLS: tuple[str, ...] = _kernel_tools_export()
SURFACE_MANIFEST: tuple[dict[str, str], ...] = _build_surface_manifest_export()


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()

# PRD-CORE-248 FR02: the app exists and its full tool surface is registered.
# Emitted after registration because registration IS app construction here —
# _register_tools() is the single largest self-time module in the import graph.
emit_boot_phase("app_constructed")
