"""Server-side ceremony enforcement middleware.

PRD-INFRA-007 + PRD-CORE-098-FR06: Tracks per-session ceremony state.
When `.trw/context/pre_compact_state.json` indicates recovery is pending,
``trw_session_start`` is required before other ``trw_*`` tools may run. Outside
that post-compaction state, tools execute normally and the middleware only adds
advisory warnings for sessions that skipped ceremony.

This is client-agnostic — works with Claude Code, Cursor, Windsurf, or
any MCP client. Middleware state is keyed by MCP session_id so parallel
connections remain isolated from one another.
"""

from __future__ import annotations

__all__ = ["CeremonyMiddleware"]

import json

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

# Session-local recovery gate state. A session that sees the post-compaction
# marker must complete its own successful session_start() even if another
# session later clears the shared disk marker.
_compaction_gate_sessions: dict[str, bool] = {}

# Sessions observed by the middleware in this server process. When a new
# compaction marker appears, every currently known session must recover again,
# even if it had already completed an earlier session_start().
_known_sessions: set[str] = set()

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
    _compaction_gate_sessions.clear()
    _known_sessions.clear()


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


def _is_compaction_gate_required() -> bool:
    """Return True when a pre-compaction marker indicates recovery is pending."""

    try:
        from trw_mcp.state._paths import resolve_trw_dir

        return (resolve_trw_dir() / "context" / "pre_compact_state.json").exists()
    except Exception:  # justified: fail-open, compaction detection must never block tool execution
        logger.debug(
            "compaction_gate_detection_failed",
            component="ceremony",
            op="detect_compaction_gate",
            outcome="fail_open",
            exc_info=True,
        )
        return False


def _clear_compaction_gate_safe() -> None:
    """Clear the pre-compaction marker once session_start succeeds."""

    try:
        from trw_mcp.state._paths import resolve_trw_dir

        marker_path = resolve_trw_dir() / "context" / "pre_compact_state.json"
        if marker_path.exists():
            marker_path.unlink()
    except Exception:  # justified: fail-open, marker cleanup must not break session start
        logger.debug(
            "compaction_gate_clear_failed",
            component="ceremony",
            op="clear_compaction_gate",
            outcome="fail_open",
            exc_info=True,
        )


def _extract_session_start_payload(result: object) -> dict[str, object] | None:
    """Best-effort extraction of a session_start payload from ToolResult."""

    structured_content = getattr(result, "structured_content", None)
    if isinstance(structured_content, dict):
        return structured_content

    content = getattr(result, "content", [])
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, TextContent):
            continue
        try:
            payload = json.loads(block.text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _session_start_succeeded(result: object) -> bool:
    """Return True when the session_start payload explicitly reports success."""

    payload = _extract_session_start_payload(result)
    if payload is None:
        return True

    success = payload.get("success")
    if isinstance(success, bool):
        return success

    status = payload.get("status")
    if isinstance(status, str):
        return status.lower() == "success"

    return True


def _is_compaction_gate_required_for_session(session_id: str) -> bool:
    """Return True when this session still owes post-compaction recovery."""

    if _is_compaction_gate_required():
        for known_session_id in _known_sessions:
            _compaction_gate_sessions[known_session_id] = True

    return _compaction_gate_sessions.get(session_id, False)


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
        _known_sessions.add(session_id)

        compaction_gate_required = _is_compaction_gate_required_for_session(session_id)

        # Ceremony tool called — mark session as active after a successful session_start
        if tool_name in CEREMONY_TOOLS:
            ceremony_result = await call_next(context)
            if _session_start_succeeded(ceremony_result):
                mark_session_active(session_id)
                _compaction_gate_sessions.pop(session_id, None)
                _clear_compaction_gate_safe()
                logger.debug(
                    "ceremony_activated",
                    op="ceremony",
                    session_id=session_id,
                    tool=tool_name,
                )
            else:
                logger.info(
                    "ceremony_activation_skipped",
                    op="ceremony",
                    session_id=session_id,
                    tool=tool_name,
                    outcome="unsuccessful_session_start",
                )
            _touch_heartbeat_safe()
            return ceremony_result

        # Post-compaction gate (PRD-CORE-098-FR06): only block trw_* tools
        # when recovery is actually pending after context compaction.
        if compaction_gate_required and tool_name.startswith("trw_"):
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
                compaction_gate_required=compaction_gate_required,
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
