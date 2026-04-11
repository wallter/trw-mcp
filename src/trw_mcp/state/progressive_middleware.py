"""FastMCP middleware for progressive tool disclosure — PRD-CORE-067.

Compact capability cards: non-hot-set tools get truncated descriptions
and empty inputSchemas in tools/list responses. Full schemas remain
registered with FastMCP — middleware only modifies the listing view.
On tool invocation, the tool is marked "expanded" so subsequent
listings return the full schema.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import mcp.types as mt
import structlog
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult

logger = structlog.get_logger(__name__)

_EMPTY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {},
    "required": [],
}

# Match first sentence: up to first period followed by whitespace or end-of-string.
_FIRST_SENTENCE_RE = re.compile(r"^(.*?\.)\s", re.DOTALL)


def truncate_description(description: str | None, max_len: int = 80) -> str:
    """Truncate description to first sentence, capped at max_len chars.

    Rules (from PRD-CORE-067 OQ-002):
    1. Take content up to and including the first period followed by
       whitespace or end-of-string.
    2. Cap at max_len characters.
    3. If no period within max_len, truncate at max_len and append "...".

    Args:
        description: Full tool description.
        max_len: Maximum character length.

    Returns:
        Truncated description string.
    """
    if not description:
        return ""

    match = _FIRST_SENTENCE_RE.match(description)
    if match:
        sentence = match.group(1)
        if len(sentence) <= max_len:
            return sentence
        return sentence[: max_len - 3] + "..."

    # No sentence-ending period found — truncate raw
    if len(description) <= max_len:
        return description
    return description[: max_len - 3] + "..."


class ProgressiveDisclosureMiddleware(Middleware):
    """Middleware that compacts non-hot-set tool listings.

    Hot set tools and already-expanded tools retain full schemas.
    All other tools get compact capability cards in tools/list responses.
    Tool invocations always work normally (full schemas are registered
    with FastMCP at startup).

    Args:
        hot_set: Set of tool names that always show full schemas.
        tool_groups: Mapping of group name to tool name lists.
    """

    def __init__(
        self,
        hot_set: set[str],
        tool_groups: dict[str, list[str]],
    ) -> None:
        super().__init__()
        self._hot_set = hot_set
        self._expanded: set[str] = set()
        self._tool_groups = tool_groups
        self._tools_used: list[str] = []

    @property
    def expanded(self) -> set[str]:
        """Set of tool names that have been expanded (for testing)."""
        return self._expanded

    @property
    def tools_used(self) -> list[str]:
        """List of tool names invoked during this session (for profiling)."""
        return list(self._tools_used)

    def expand_group(self, group: str) -> tuple[list[str], list[str]]:
        """Expand all tools in a capability group.

        Args:
            group: Group name (ceremony, learning, orchestration,
                requirements, build).

        Returns:
            Tuple of (newly_expanded, already_expanded) tool name lists.

        Raises:
            ValueError: If group name is not recognized.
        """
        if group not in self._tool_groups:
            valid = sorted(self._tool_groups.keys())
            msg = f"Unknown group: {group!r}. Valid groups: {valid}"
            raise ValueError(msg)

        tools = self._tool_groups[group]
        newly_expanded: list[str] = []
        already_expanded: list[str] = []

        for tool_name in tools:
            if tool_name in self._expanded or tool_name in self._hot_set:
                already_expanded.append(tool_name)
            else:
                self._expanded.add(tool_name)
                newly_expanded.append(tool_name)

        return newly_expanded, already_expanded

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        """Intercept tools/list to compact non-hot-set, non-expanded tools."""
        tools = await call_next(context)
        result: list[Tool] = []

        for tool in tools:
            if tool.name in self._hot_set or tool.name in self._expanded:
                result.append(tool)
            else:
                compact_tool = tool.model_copy(
                    update={
                        "description": truncate_description(tool.description),
                        "parameters": dict(_EMPTY_SCHEMA),
                        "meta": {**(tool.meta or {}), "_compact": True},
                    },
                )
                result.append(compact_tool)

        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Track tool usage and mark invoked tools as expanded."""
        tool_name = context.message.name
        self._expanded.add(tool_name)
        self._tools_used.append(tool_name)
        return await call_next(context)
