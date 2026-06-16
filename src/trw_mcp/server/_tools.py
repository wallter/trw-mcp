"""Tool, resource, and prompt registration for the TRW MCP server.

All tools, resources, and prompts are registered eagerly at import
so they are available via ``fastmcp run`` and test imports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

import structlog

from trw_mcp.server._app import mcp

logger = structlog.get_logger(__name__)
_AsyncResultT = TypeVar("_AsyncResultT")


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


def _apply_tool_exposure_filter() -> None:
    """PRD-CORE-125-FR02: Remove tools not in the active exposure preset.

    Reads ``effective_tool_exposure_mode`` from config, resolves the
    corresponding tool preset from ``TOOL_PRESETS``, and removes any
    registered tool not in the allowed set.  Mode ``"all"`` is a no-op.
    Mode ``"custom"`` uses the explicit ``tool_exposure_list`` from config.

    Fail-CLOSED under a restrictive mode: an operator that configured a
    non-``all`` mode meant to HIDE privileged tools. If anything fails AFTER
    we have determined the mode is restrictive, we must NOT leave every tool
    registered — that would silently widen exposure (under-block). Instead we
    fall back to the most restrictive known preset (``core``) so a config or
    list_tools failure can never expose more than the operator asked for, and
    we log the failure at WARNING (not DEBUG) so the loss of fidelity is
    visible. When the mode is ``all`` / unset / unresolvable, keeping all
    tools is the intended behavior.
    """
    # Phase 1: resolve the mode. A failure HERE means we don't know what the
    # operator wanted, so leaving all tools registered is acceptable (we have
    # no evidence a restrictive mode was configured) — keep this fail-open.
    try:
        from trw_mcp.models.config import get_config

        mode = get_config().effective_tool_exposure_mode
    except Exception:  # justified: mode unknown -> no evidence of restrictive intent
        logger.warning("tool_exposure_mode_resolve_failed", exc_info=True)  # justified: fail-open, visible
        return

    if mode == "all":
        return

    # Phase 2: from here on we KNOW a restrictive mode is configured. Any
    # failure must fail to the SAFE (most restrictive) subset, never to "all".
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.models.config._defaults import TOOL_PRESETS

        config = get_config()

        if mode == "custom":
            allowed = set(config.tool_exposure_list)
        else:
            preset = TOOL_PRESETS.get(mode)
            if preset is None:
                # Unknown restrictive mode: do not widen — fall to safe subset.
                logger.warning("unknown_tool_exposure_mode_failing_safe", mode=mode)
                allowed = set(TOOL_PRESETS["core"])
            else:
                allowed = set(preset)

        if not allowed:
            # An empty allow-set under a restrictive mode would remove every
            # tool; treat as a misconfiguration and fail to the safe subset
            # rather than serving everything.
            logger.warning("empty_tool_exposure_set_failing_safe", mode=mode)
            allowed = set(TOOL_PRESETS["core"])

        # Get all currently registered tool names via the public async API.
        # FastMCP's internal tool-manager attributes changed across releases,
        # so stdio startup must not depend on private state here.
        registered_tools = [t.name for t in _run_async(mcp.list_tools())]
        removed: list[str] = []
        for tool_name in registered_tools:
            if tool_name not in allowed:
                mcp.remove_tool(tool_name)
                removed.append(tool_name)

        if removed:
            logger.info(
                "tool_exposure_filter_applied",
                mode=mode,
                allowed_count=len(allowed),
                removed_count=len(removed),
                removed=removed[:10],  # Log at most 10 names to avoid spam
            )
    except Exception:
        # CONSERVATIVE / fail-closed: a restrictive mode was configured but the
        # filter blew up. Make a best-effort pass to remove anything outside
        # the safe ``core`` subset so a failure here cannot leave privileged
        # tools exposed. Log at WARNING so the degradation is visible.
        logger.warning("tool_exposure_filter_failed_failing_safe", mode=mode, exc_info=True)
        try:
            from trw_mcp.models.config._defaults import TOOL_PRESETS

            safe = set(TOOL_PRESETS["core"])
            for tool in list(_run_async(mcp.list_tools())):
                if tool.name not in safe:
                    mcp.remove_tool(tool.name)
        except Exception:  # justified: last-resort; could not enumerate tools to prune
            logger.warning("tool_exposure_safe_fallback_failed", mode=mode, exc_info=True)


def _register_tools() -> None:
    """Register all tools, resources, and prompts on the MCP server."""
    from trw_mcp.prompts.aaref import register_aaref_prompts
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources
    from trw_mcp.tools.agent_work_evidence import register_agent_work_evidence_tools
    from trw_mcp.tools.before_edit_hint import register_before_edit_hint_tools
    from trw_mcp.tools.before_edit_hint_batch import (
        register_before_edit_hint_batch_tools,
    )
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.ceremony_feedback import register_ceremony_feedback_tools
    from trw_mcp.tools.channel_render import register_channel_render_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.code_index import register_code_index_tools
    from trw_mcp.tools.code_search import register_code_search_tools
    from trw_mcp.tools.codebase_risk_report import register_codebase_risk_report_tools
    from trw_mcp.tools.cross_repo_ordering import register_cross_repo_ordering_tools
    from trw_mcp.tools.entity_risk_map import register_entity_risk_map_tools
    from trw_mcp.tools.knowledge import register_knowledge_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.mcp_security_status import register_mcp_security_status
    from trw_mcp.tools.meta_tune_ops import register_meta_tune_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.ordering_compare import register_ordering_compare_tools
    from trw_mcp.tools.query_tools import register_query_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.review import register_review_tools
    from trw_mcp.tools.skill_discovery import register_skill_discovery_tools
    from trw_mcp.tools.submit_feedback import register_submit_feedback_tools

    register_build_tools(mcp)
    register_ceremony_tools(mcp)
    # PRD-CORE-069-FR06/FR08 (FIX-051): human-in-the-loop ceremony de-escalation
    # — operator status / approve / revert kill-switch tools.
    register_ceremony_feedback_tools(mcp)
    register_checkpoint_tools(mcp)
    register_learning_tools(mcp)
    register_meta_tune_tools(mcp)
    register_knowledge_tools(mcp)
    register_orchestration_tools(mcp)
    register_requirements_tools(mcp)
    register_review_tools(mcp)
    # PRD-HPO-MEAS-001 FR-7 + FR-8: cross-session event query + surface diff
    register_query_tools(mcp)
    # PRD-INFRA-SEC-001 FR-5: operator status tool for MCP security layer
    register_mcp_security_status(mcp)
    # PRD-DIST-1983 (c746): trw-distill before-edit hint consumer (tier-gated)
    register_before_edit_hint_tools(mcp)
    # PRD-DIST-1989 (c747): batch sibling of trw_before_edit_hint
    register_before_edit_hint_batch_tools(mcp)
    # PRD-DIST-1990 (c747): trw-distill ranked risk report consumer
    register_codebase_risk_report_tools(mcp)
    # PRD-DIST-1994 (c748): trw-distill ordering-compare consumer (4th wire)
    register_ordering_compare_tools(mcp)
    # PRD-DIST-1995 (c748): trw-distill cross-repo-ordering consumer (5th wire)
    register_cross_repo_ordering_tools(mcp)
    # PRD-CORE-171: local SHA-256 code-index manifest update tool
    register_code_index_tools(mcp)
    # PRD-CORE-172: local indexed lexical/symbol code search
    register_code_search_tools(mcp)
    # PRD-CORE-167: public entity-risk sidecar consumer
    register_entity_risk_map_tools(mcp)
    # PRD-CORE-168: privacy-safe canonical agent work evidence export
    register_agent_work_evidence_tools(mcp)
    # PRD-CORE-170: read-only skill manifest discovery helper
    register_skill_discovery_tools(mcp)
    # PRD-CORE-182 + PRD-INFRA-132 FR04: backend submission portal client
    # (PII redaction added in-place per PRD-INFRA-132 FR04a)
    register_submit_feedback_tools(mcp)
    # PRD-DIST-2400 FR17: channel manifest render MCP tool
    register_channel_render_tools(mcp)
    # PRD-DIST-2400 §meta-tune: channel correlation + throttle stats MCP tool
    from trw_mcp.tools.channel_stats import register_channel_stats_tools

    register_channel_stats_tools(mcp)

    # PRD-FIX-COMPOUNDING-6 FR02: unified compounding-pipeline health probe tool
    from trw_mcp.tools._pipeline_health_tool import register_pipeline_health_tools

    register_pipeline_health_tools(mcp)

    # PRD-CORE-144: empirical probe harness — bounded sandboxed experiments
    # for disputed plan assumptions (consumes the shared SAFE-001
    # ProbeIsolationContext). Registers trw_probe + trw_probe_budget_status.
    from trw_mcp.tools.trw_probe import register_probe_tools

    register_probe_tools(mcp)

    # PRD-HPO-PROF-001 FR-4/FR-11: hierarchical profile explain tool
    from trw_mcp.tools.trw_profile_explain import register_trw_profile_explain_tools

    register_trw_profile_explain_tools(mcp)

    # PRD-INTENT-002 FR06: phase-exposure override tool (trw_request_tool_access)
    from trw_mcp.tools.phase_overrides import register_phase_override_tools

    register_phase_override_tools(mcp)

    register_config_resources(mcp)
    register_run_state_resources(mcp)
    register_template_resources(mcp)

    register_aaref_prompts(mcp)

    # PRD-CORE-125-FR02: Apply tool exposure filter after all tools are registered.
    _apply_tool_exposure_filter()

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


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()
