"""Server-side ceremony enforcement middleware.

PRD-INFRA-007: Tracks per-session ceremony state and prepends a warning
to every tool response until the agent calls a ceremony-initializing tool
(trw_session_start, trw_init, or trw_recall).

This is client-agnostic — works with Claude Code, Cursor, Windsurf, or
any MCP client. FastMCP per-session state is inherently isolated per
connection, so parallel sessions are tracked independently.
"""

from __future__ import annotations

from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams, TextContent

# Module-level session state: session_id -> True (ceremony completed).
# MCP connections are short-lived (1 per Claude Code session), so this
# dict stays small (1-3 entries max). No cleanup needed.
_session_state: dict[str, bool] = {}

# Tools that ARE the ceremony — calling them marks the session as active.
CEREMONY_TOOLS: frozenset[str] = frozenset({
    "trw_session_start",
    "trw_init",
    "trw_recall",
})

# Warning prepended to every non-exempt tool response when ceremony
# has not been run. Value-oriented framing — explains what the agent gains
# by calling session_start, rather than threatening consequences.
# Loaded from centralized messages.yaml with inline fallback.
_DEFAULT_CEREMONY_WARNING = (
    "trw_session_start() has not been called yet for this session.\n"
    "Without it, you are working without:\n"
    "  - Prior session learnings (patterns and gotchas that prevent re-work)\n"
    "  - Active run state (phase, progress, last checkpoint)\n"
    "Call trw_session_start() to load your context, then retry this operation."
)


def _load_ceremony_warning() -> str:
    """Load ceremony warning from centralized messages, with inline fallback."""
    from trw_mcp.prompts.messaging import get_message_or_default

    return get_message_or_default("ceremony_warning", _DEFAULT_CEREMONY_WARNING)


CEREMONY_WARNING = _load_ceremony_warning()


def mark_session_active(session_id: str) -> None:
    """Mark a session as having completed ceremony."""
    _session_state[session_id] = True


def is_session_active(session_id: str) -> bool:
    """Return True if ceremony has been run for this session."""
    return _session_state.get(session_id, False)


def reset_state() -> None:
    """Clear all session state — for testing only."""
    _session_state.clear()


class CeremonyMiddleware(Middleware):
    """FastMCP middleware that enforces session ceremony.

    For every tool call:
    - If the tool is a ceremony tool, mark the session as active.
    - If the session is NOT active and the tool is NOT exempt,
      prepend a warning TextContent block to the tool result.
    - If fastmcp_context is None (unit tests), do nothing.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls to enforce ceremony state."""
        tool_name = context.message.name
        ctx = context.fastmcp_context

        # Graceful fallback: no MCP session context (unit tests, direct calls)
        if ctx is None or ctx.request_context is None:
            return await call_next(context)

        session_id = ctx.session_id

        # Ceremony tool called — mark session as active
        if tool_name in CEREMONY_TOOLS:
            mark_session_active(session_id)
            return await call_next(context)

        # Execute the tool
        result: ToolResult = await call_next(context)

        # If session is NOT active, prepend warning
        if not is_session_active(session_id):
            warning_block = TextContent(type="text", text=CEREMONY_WARNING)
            result.content.insert(0, warning_block)

        return result
