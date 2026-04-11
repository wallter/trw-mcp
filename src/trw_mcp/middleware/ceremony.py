"""Server-side ceremony enforcement middleware.

PRD-INFRA-007 + PRD-CORE-098-FR06: Tracks per-session ceremony state.
Only ``trw_session_start`` clears the post-compaction gate. Until the session
is active:
- trw_* tools (non-ceremony) are BLOCKED with an error response.
- Non-trw_* tools get a warning prepended but still execute.

This is client-agnostic — works with Claude Code, Cursor, Windsurf, or
any MCP client. FastMCP per-session state is inherently isolated per
connection, so parallel sessions are tracked independently.
"""

from __future__ import annotations

__all__ = ["CeremonyMiddleware"]

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams, TextContent

# Module-level session state: session_id -> True (ceremony completed).
# MCP connections are short-lived (1 per Claude Code session), so this
# dict stays small (1-3 entries max). No cleanup needed.
_session_state: dict[str, bool] = {}

# Tools that clear the ceremony gate.
CEREMONY_TOOLS: frozenset[str] = frozenset({"trw_session_start"})

# Warning prepended to every non-exempt tool response when ceremony
# has not been run. Value-oriented framing — explains what the agent gains
# by calling session_start, rather than threatening consequences.
# Loaded from centralized messages.yaml with inline fallback.
_DEFAULT_CEREMONY_WARNING = (
    "trw_session_start() has not been called yet for this session.\n"
    "Without it, you are working without:\n"
    "  - Prior session learnings (patterns and gotchas that prevent re-work)\n"
    "  - Active run state (phase, progress, last checkpoint)\n"
    "Call trw_session_start() now \u2014 it takes one call and gives you the full context"
    " accumulated from all prior sessions."
)


def _load_ceremony_warning() -> str:
    """Load ceremony warning from centralized messages, with inline fallback."""
    from trw_mcp.prompts.messaging import get_message_or_default

    return get_message_or_default("ceremony_warning", _DEFAULT_CEREMONY_WARNING)


CEREMONY_WARNING = _load_ceremony_warning()

logger = structlog.get_logger(__name__)


def mark_session_active(session_id: str) -> None:
    """Mark a session as having completed ceremony."""
    _session_state[session_id] = True


def is_session_active(session_id: str) -> bool:
    """Return True if ceremony has been run for this session."""
    return _session_state.get(session_id, False)


def reset_state() -> None:
    """Clear all session state — for testing only."""
    _session_state.clear()


def _touch_heartbeat_safe() -> None:
    """Touch the heartbeat file for the active run (PRD-QUAL-050-FR01).

    Deferred import avoids circular dependency between middleware and state.
    Completely fail-open — never blocks tool execution.
    """
    try:
        from trw_mcp.state._paths import touch_heartbeat

        touch_heartbeat()
    except Exception:  # justified: fail-open -- heartbeat must never block tool execution
        logger.warning("heartbeat_middleware_failed", exc_info=True)


class CeremonyMiddleware(Middleware):
    """FastMCP middleware that enforces session ceremony.

    For every tool call:
    - If the tool is a ceremony tool, mark the session as active.
    - If the session is NOT active and the tool is a trw_* tool (non-ceremony),
      return an error response instead of executing (PRD-CORE-098-FR06).
    - If the session is NOT active and the tool is NOT a trw_* tool,
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
            logger.debug("ceremony_activated", op="ceremony", session_id=session_id, tool=tool_name)
            ceremony_result = await call_next(context)
            _touch_heartbeat_safe()
            return ceremony_result

        # Post-compaction gate (PRD-CORE-098-FR06): block trw_* tools
        # until session_start is called. This prevents agents from using
        # tools without prior learnings after context compaction.
        if not is_session_active(session_id) and tool_name.startswith("trw_"):
            error_payload = {
                "error": "session_start_required",
                "message": (
                    "Call trw_session_start() to load your prior learnings"
                    " before using other tools. This ensures you don't repeat"
                    " solved problems or miss known gotchas."
                ),
                "tool_attempted": tool_name,
            }
            logger.info(
                "ceremony_gate_blocked",
                op="ceremony",
                session_id=session_id,
                tool=tool_name,
            )
            return ToolResult(
                content=[TextContent(type="text", text=error_payload["message"])],
                structured_content=error_payload,
            )

        # Execute the tool
        result: ToolResult = await call_next(context)

        # Post-tool heartbeat: signal session liveness (PRD-QUAL-050-FR01)
        _touch_heartbeat_safe()

        # If session is NOT active (non-trw tool), prepend warning
        if not is_session_active(session_id):
            warning_block = TextContent(type="text", text=CEREMONY_WARNING)
            result.content.insert(0, warning_block)
            logger.debug("ceremony_warning_injected", op="ceremony", session_id=session_id, tool=tool_name)

        return result
