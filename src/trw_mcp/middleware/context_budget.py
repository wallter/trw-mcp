"""Observation masking middleware — reduce tool response verbosity in long sessions.

Tracks per-session tool call counts and applies progressive verbosity tiers:
- FULL (turns 1-N): pass through unchanged
- COMPACT (turns N+1-M): strip metadata keys, truncate long strings
- MINIMAL (turns M+1+): aggressive truncation, strip deep nesting

Redundancy detection: if a tool returns the same content as its previous call
within the session, replace with a short placeholder.

Fails open: if any compression logic raises, the original content is preserved.
"""

from __future__ import annotations

__all__ = ["ContextBudgetMiddleware"]

from typing import Literal

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams, TextContent

from trw_mcp.middleware._compression import compress_text_block, hash_content

logger = structlog.get_logger(__name__)

# ── Module-level session state ──────────────────────────────────────────
# session_id -> tool call count
_turn_counts: dict[str, int] = {}
# session_id -> {tool_name -> (last_response_hash, turn_number)}
_response_hashes: dict[str, dict[str, tuple[str, int]]] = {}


def get_turn_count(session_id: str) -> int:
    """Return the current turn count for a session."""
    return _turn_counts.get(session_id, 0)


def reset_state() -> None:
    """Clear all module-level state — for testing only."""
    _turn_counts.clear()
    _response_hashes.clear()


def get_verbosity_tier(
    turn_count: int,
    compact_after: int = 10,
    minimal_after: int = 30,
) -> Literal["full", "compact", "minimal"]:
    """Determine verbosity tier from turn count and thresholds."""
    if turn_count > minimal_after:
        return "minimal"
    if turn_count > compact_after:
        return "compact"
    return "full"


class ContextBudgetMiddleware(Middleware):
    """FastMCP middleware that applies observation masking.

    Tracks per-session tool call counts and progressively compresses
    tool responses as the session grows longer, conserving context window
    budget. Also detects redundant responses within a session.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool responses to apply observation masking."""
        tool_name: str = context.message.name
        ctx = context.fastmcp_context

        # No MCP session context (unit tests, direct calls) — pass through
        if ctx is None or ctx.request_context is None:
            return await call_next(context)

        session_id: str = ctx.session_id

        # Increment turn count
        _turn_counts[session_id] = _turn_counts.get(session_id, 0) + 1
        turn = _turn_counts[session_id]

        # Get the tool result
        result: ToolResult = await call_next(context)

        try:
            return self._apply_masking(result, session_id, tool_name, turn)
        except Exception:  # justified: fail-open, observation masking must never block tool results
            logger.debug(
                "context_budget_masking_failed", op="observation_masking", tool=tool_name, turn=turn, exc_info=True
            )
            return result

    def _apply_masking(
        self,
        result: ToolResult,
        session_id: str,
        tool_name: str,
        turn: int,
    ) -> ToolResult:
        """Apply redundancy detection and verbosity compression."""
        try:
            from trw_mcp.models.config import get_config

            config = get_config()
            if not config.observation_masking:
                return result
            compact_after = config.compact_after_turns
            minimal_after = config.minimal_after_turns
        except Exception:  # justified: fail-open, config load failure uses safe defaults rather than blocking
            logger.debug("context_budget_config_load_failed", exc_info=True)
            compact_after = 10
            minimal_after = 30

        # Redundancy detection (before compression, on raw content)
        content_hash = hash_content(result.content)
        session_hashes = _response_hashes.setdefault(session_id, {})
        prev = session_hashes.get(tool_name)

        if prev is not None and prev[0] == content_hash:
            logger.debug(
                "context_budget_redundancy_detected",
                op="observation_masking",
                tool=tool_name,
                turn=turn,
                prev_turn=prev[1],
            )
            result.content = [
                TextContent(
                    type="text",
                    text=f"[No changes since turn {prev[1]}]",
                )
            ]
            return result

        # Store hash for this tool
        session_hashes[tool_name] = (content_hash, turn)

        # Determine verbosity tier
        tier = get_verbosity_tier(turn, compact_after, minimal_after)
        if tier == "full":
            return result

        # Apply compression to TextContent blocks
        for i, block in enumerate(result.content):
            if not isinstance(block, TextContent):
                continue
            compressed = compress_text_block(block.text, tier)
            if compressed != block.text:
                result.content[i] = TextContent(type="text", text=compressed)

        return result
