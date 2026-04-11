"""Tool, resource, and prompt registration for the TRW MCP server.

All 24 tools, 6 resources, and prompts are registered eagerly at import
so they are available via ``fastmcp run`` and test imports.
"""

from __future__ import annotations

import structlog

from trw_mcp.server._app import _middleware_list, mcp

logger = structlog.get_logger(__name__)


def _apply_tool_exposure_filter() -> None:
    """PRD-CORE-125-FR02: Remove tools not in the active exposure preset.

    Reads ``effective_tool_exposure_mode`` from config, resolves the
    corresponding tool preset from ``TOOL_PRESETS``, and removes any
    registered tool not in the allowed set.  Mode ``"all"`` is a no-op.
    Mode ``"custom"`` uses the explicit ``tool_exposure_list`` from config.

    Fail-open: if config loading fails, all tools remain registered.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.models.config._defaults import TOOL_PRESETS

        config = get_config()
        mode = config.effective_tool_exposure_mode

        if mode == "all":
            return

        if mode == "custom":
            allowed = set(config.tool_exposure_list)
        else:
            preset = TOOL_PRESETS.get(mode)
            if preset is None:
                logger.warning("unknown_tool_exposure_mode", mode=mode)
                return
            allowed = set(preset)

        if not allowed:
            logger.warning("empty_tool_exposure_set", mode=mode)
            return

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
    except Exception:  # justified: fail-open, tool filtering must never crash server startup
        logger.debug("tool_exposure_filter_failed", exc_info=True)


def _register_tools() -> None:
    """Register all tools, resources, and prompts on the MCP server."""
    from trw_mcp.prompts.aaref import register_aaref_prompts
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.ceremony_feedback import register_ceremony_feedback_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.knowledge import register_knowledge_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.report import register_report_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.review import register_review_tools
    from trw_mcp.tools.usage import register_usage_tools, set_progressive_middleware

    register_build_tools(mcp)
    register_ceremony_tools(mcp)
    register_ceremony_feedback_tools(mcp)
    register_checkpoint_tools(mcp)
    register_knowledge_tools(mcp)
    register_learning_tools(mcp)
    register_orchestration_tools(mcp)
    register_report_tools(mcp)
    register_requirements_tools(mcp)
    register_review_tools(mcp)
    register_usage_tools(mcp)

    register_config_resources(mcp)
    register_run_state_resources(mcp)
    register_template_resources(mcp)

    register_aaref_prompts(mcp)

    # Wire progressive disclosure middleware reference for expand tool (CORE-067)
    from trw_mcp.state.progressive_middleware import ProgressiveDisclosureMiddleware

    for mw in _middleware_list:
        if isinstance(mw, ProgressiveDisclosureMiddleware):
            set_progressive_middleware(mw)
            break

    # PRD-CORE-125-FR02: Apply tool exposure filter after all tools are registered.
    _apply_tool_exposure_filter()


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()
