"""Response optimizer middleware — compact JSON for LLM consumption.

Intercepts all tool call responses and optimizes JSON TextContent blocks:
- Rounds floats to 2 decimal places (sufficient for LLM consumption)
- Strips dict keys with None values (removes noise)
- Strips dict keys with empty dicts or empty lists (removes noise)
- Re-serializes to compact JSON (no whitespace)

Non-JSON text and non-TextContent blocks pass through untouched.
Fails open: if JSON parsing fails, the original content is preserved.
"""

from __future__ import annotations

import json

import structlog

from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams, TextContent


def _is_empty(v: object) -> bool:
    """True for None, empty dict, or empty list. NOT for 0, False, empty string."""
    if v is None:
        return True
    if isinstance(v, dict) and len(v) == 0:
        return True
    return bool(isinstance(v, list) and len(v) == 0)


def _compact(data: object) -> object:
    """Recursively round floats and strip null/empty values from dicts."""
    if isinstance(data, dict):
        return {
            k: _compact(v)
            for k, v in data.items()
            if not _is_empty(v)
        }
    if isinstance(data, list):
        return [_compact(item) for item in data]
    if isinstance(data, float):
        return round(data, 2)
    return data


logger = structlog.get_logger(__name__)


class ResponseOptimizerMiddleware(Middleware):
    """FastMCP middleware that compacts JSON tool responses.

    For every tool call response, iterates over content blocks and
    optimizes any TextContent whose text is valid JSON by rounding
    floats and stripping null/empty values.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool responses to compact JSON content."""
        result: ToolResult = await call_next(context)

        for i, block in enumerate(result.content):
            if not isinstance(block, TextContent):
                continue

            text = block.text
            if not text or (text[0] not in ("{", "[")):
                continue

            try:
                parsed = json.loads(text)
                compacted = _compact(parsed)
                result.content[i] = TextContent(
                    type="text",
                    text=json.dumps(compacted, separators=(",", ":")),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                # Fail open: leave content untouched
                continue

        return result
