"""Tool, resource, and prompt registration for the TRW MCP server.

All 23 tools, 6 resources, and prompts are registered eagerly at import
so they are available via ``fastmcp run`` and test imports.
"""

from __future__ import annotations

from trw_mcp.server._app import _middleware_list, mcp


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


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()
