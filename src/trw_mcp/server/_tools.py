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
    from trw_mcp.tools._pipeline_health_tool import register_pipeline_health_tools
    from trw_mcp.tools.agent_work_evidence import register_agent_work_evidence_tools
    from trw_mcp.tools.before_edit_hint import register_before_edit_hint_tools
    from trw_mcp.tools.before_edit_hint_batch import (
        register_before_edit_hint_batch_tools,
    )
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.ceremony_feedback import register_ceremony_feedback_tools
    from trw_mcp.tools.channel_render import register_channel_render_tools
    from trw_mcp.tools.channel_stats import register_channel_stats_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.code_index import register_code_index_tools
    from trw_mcp.tools.code_search import register_code_search_tools
    from trw_mcp.tools.codebase_risk_report import register_codebase_risk_report_tools
    from trw_mcp.tools.cross_repo_ordering import register_cross_repo_ordering_tools
    from trw_mcp.tools.delivery_ops import register_delivery_tools
    from trw_mcp.tools.dispatch import register_dispatch_tools
    from trw_mcp.tools.entity_risk_map import register_entity_risk_map_tools
    from trw_mcp.tools.knowledge import register_knowledge_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.mcp_security_status import register_mcp_security_status
    from trw_mcp.tools.meta_tune_ops import register_meta_tune_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.ordering_compare import register_ordering_compare_tools
    from trw_mcp.tools.phase_overrides import register_phase_override_tools
    from trw_mcp.tools.query_tools import register_query_tools
    from trw_mcp.tools.replay import register_replay_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.review import register_review_tools
    from trw_mcp.tools.skill_discovery import register_skill_discovery_tools
    from trw_mcp.tools.submit_feedback import register_submit_feedback_tools
    from trw_mcp.tools.trw_probe import register_probe_tools
    from trw_mcp.tools.trw_profile_explain import register_trw_profile_explain_tools

    return (
        register_build_tools,
        register_ceremony_tools,
        # PRD-CORE-069-FR06/FR08 (FIX-051): human-in-the-loop ceremony
        # de-escalation — operator status / approve / revert kill-switch tools.
        register_ceremony_feedback_tools,
        register_checkpoint_tools,
        register_learning_tools,
        register_meta_tune_tools,
        register_knowledge_tools,
        register_orchestration_tools,
        register_requirements_tools,
        register_replay_tools,
        register_review_tools,
        # PRD-HPO-MEAS-001 FR-7 + FR-8: cross-session event query + surface diff
        register_query_tools,
        # PRD-INFRA-SEC-001 FR-5: operator status tool for MCP security layer
        register_mcp_security_status,
        # PRD-DIST-1983 (c746): trw-distill before-edit hint consumer (tier-gated)
        register_before_edit_hint_tools,
        # PRD-DIST-1989 (c747): batch sibling of trw_before_edit_hint
        register_before_edit_hint_batch_tools,
        # PRD-DIST-1990 (c747): trw-distill ranked risk report consumer
        register_codebase_risk_report_tools,
        # PRD-DIST-1994 (c748): trw-distill ordering-compare consumer (4th wire)
        register_ordering_compare_tools,
        # PRD-DIST-1995 (c748): trw-distill cross-repo-ordering consumer (5th wire)
        register_cross_repo_ordering_tools,
        # PRD-CORE-171: local SHA-256 code-index manifest update tool
        register_code_index_tools,
        # PRD-CORE-172: local indexed lexical/symbol code search
        register_code_search_tools,
        # PRD-CORE-167: public entity-risk sidecar consumer
        register_entity_risk_map_tools,
        # PRD-CORE-168: privacy-safe canonical agent work evidence export
        register_agent_work_evidence_tools,
        # PRD-CORE-170: read-only skill manifest discovery helper
        register_skill_discovery_tools,
        # PRD-CORE-182 + PRD-INFRA-132 FR04: backend submission portal client
        # (PII redaction added in-place per PRD-INFRA-132 FR04a)
        register_submit_feedback_tools,
        # PRD-DIST-2400 FR17: channel manifest render MCP tool
        register_channel_render_tools,
        # PRD-DIST-2400 §meta-tune: channel correlation + throttle stats MCP tool
        register_channel_stats_tools,
        # PRD-FIX-COMPOUNDING-6 FR02: unified compounding-pipeline health probe
        register_pipeline_health_tools,
        # PRD-CORE-144: empirical probe harness (trw_probe + budget status).
        register_probe_tools,
        # PRD-HPO-PROF-001 FR-4/FR-11: hierarchical profile explain tool
        register_trw_profile_explain_tools,
        # PRD-INTENT-002 FR06: phase-exposure override (trw_request_tool_access)
        register_phase_override_tools,
        # Cross-client dispatch Phase 3: dispatch launcher MCP tools.
        register_dispatch_tools,
        # PRD-CORE-208 FR04/FR05: read-only delivery status + guarded recovery.
        register_delivery_tools,
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


def _assert_manifest_parity() -> None:
    """PRD-CORE-218 FR01: log any drift between the manifest and registration.

    The authoritative bidirectional parity assertion lives in the FR01
    acceptance test. At boot we only emit a visible WARNING on drift and never
    raise — a manifest bookkeeping lapse must not brick server startup.
    """
    try:
        from trw_mcp.server._surface_manifest_registry import MANIFEST_BY_NAME

        registered = raw_registered_tool_names()
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
    _assert_manifest_parity()

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
    # releases, so the rewrap here is best-effort. The authoritative
    # consult path is the inner-body call added directly to the sprint-96
    # tools (``trw_query_events``, ``trw_surface_diff``,
    # ``trw_mcp_security_status``) in their registrars. This pass attempts
    # to extend the same coverage to any other tool whose underlying
    # callable we can resolve — silently skipping if the FastMCP version
    # does not expose a rewrap point.
    _apply_security_consult_wrapping()


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
# ``resolved_profile_from_manifest_seam``). capability_class here is the
# STATIC admission view: kernel is always available, high-risk packs are
# operator-gated, every other pack is discoverable via skill discovery /
# request_tool_access. Task-scoped availability is layered on top by FR03.
# ---------------------------------------------------------------------------


def _static_capability_class(name: str, pack: str) -> str:
    from trw_mcp.models.config._defaults import HIGH_RISK_PACKS
    from trw_mcp.server._surface_manifest_registry import _KERNEL_TOOLS

    if name in _KERNEL_TOOLS:
        return "available"
    if pack in HIGH_RISK_PACKS:
        return "gated"
    return "discoverable"


def _build_surface_manifest_export() -> tuple[dict[str, str], ...]:
    from trw_mcp.server._surface_manifest_registry import TOOL_MANIFEST

    return tuple(
        {
            "tool_id": entry.name,
            "pack": entry.pack,
            "capability_class": _static_capability_class(entry.name, entry.pack),
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
