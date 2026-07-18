"""List-tools filtering helpers for the mounted MCP security middleware."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
from fastmcp.tools import Tool
from mcp.types import ListToolsRequest

from trw_mcp.middleware._mcp_security_helpers import (
    AdvertisedTool,
    normalize_tool_name,
    resolve_run_context,
    resolve_transport_from_ctx,
)


def safe_session_id(fastmcp_ctx: object | None) -> str:
    """Return the active context session id without requiring a live request."""
    if fastmcp_ctx is None:
        return ""
    try:
        value = fastmcp_ctx.session_id  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return ""
    return value if isinstance(value, str) else ""


async def filter_listed_tools(
    middleware: Any,
    context: MiddlewareContext[ListToolsRequest],
    call_next: CallNext[ListToolsRequest, Sequence[Tool]],
    active_phase_resolver: Any,
) -> Sequence[Tool]:
    """Apply registry, transport, and phase filtering to listed tools."""
    tools = list(await call_next(context))
    fastmcp_ctx = context.fastmcp_context
    sid = safe_session_id(fastmcp_ctx)
    _, run_id = resolve_run_context(
        configured_run_dir=middleware._run_dir,
        session_id=sid,
        fastmcp_context=fastmcp_ctx,
    )
    allowed_ads = middleware.filter_advertised_tools(
        transport=resolve_transport_from_ctx(fastmcp_ctx),
        advertisements=[AdvertisedTool(server=middleware.default_server_name, name=tool.name) for tool in tools],
        session_id=sid,
        run_id=run_id,
        current_phase=active_phase_resolver(session_id=sid, fastmcp_context=fastmcp_ctx),
        fastmcp_context=fastmcp_ctx,
    )
    allowed_names = {ad.name for ad in allowed_ads}
    return [tool for tool in tools if normalize_tool_name(tool.name) in allowed_names]
