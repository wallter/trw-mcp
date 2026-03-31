"""Response optimizer middleware — compact JSON/YAML for LLM consumption.

Intercepts all tool call responses and optimizes JSON TextContent blocks:
- Rounds floats to 2 decimal places (sufficient for LLM consumption)
- Strips dict keys with None values (removes noise)
- Strips dict keys with empty dicts or empty lists (removes noise)
- Re-serializes to compact JSON or YAML based on config (PRD-CORE-096)

Non-JSON text and non-TextContent blocks pass through untouched.
Fails open: if JSON parsing or YAML serialization fails, the original
content or JSON fallback is preserved.
"""

from __future__ import annotations

__all__ = ["ResponseOptimizerMiddleware"]

import io
import json

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams, TextContent
from ruamel.yaml import YAML


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

# Shared YAML instance — typ="safe" ensures no !!python/ tags in output.
_yaml = YAML(typ="safe")
_yaml.default_flow_style = False


def _yaml_dump(data: object) -> str:
    """Serialize data to YAML string. Falls back to compact JSON on error."""
    try:
        buf = io.StringIO()
        _yaml.dump(data, buf)
        return buf.getvalue()
    except Exception as exc:  # justified: fail-open — YAML fallback to JSON
        logger.warning("yaml_serialization_fallback", error=str(exc), fallback="json")
        return json.dumps(data, separators=(",", ":"))


def _get_response_format() -> str:
    """Read response format from config, defaulting to 'yaml'."""
    try:
        from trw_mcp.models.config import get_config

        return str(get_config().response_format)
    except Exception:  # justified: fail-open — config unavailable defaults to yaml
        return "yaml"


class ResponseOptimizerMiddleware(Middleware):
    """FastMCP middleware that compacts tool responses to JSON or YAML.

    For every tool call response, iterates over content blocks and
    optimizes any TextContent whose text is valid JSON by rounding
    floats and stripping null/empty values, then re-serializes to
    YAML (default) or compact JSON based on config.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool responses to compact and re-serialize content."""
        result: ToolResult = await call_next(context)
        fmt = _get_response_format()

        for i, block in enumerate(result.content):
            if not isinstance(block, TextContent):
                continue

            text = block.text
            if not text or (text[0] not in ("{", "[")):
                continue

            try:
                parsed = json.loads(text)
                compacted = _compact(parsed)
                if fmt == "yaml":
                    serialized = _yaml_dump(compacted)
                else:
                    serialized = json.dumps(compacted, separators=(",", ":"))
                result.content[i] = TextContent(
                    type="text",
                    text=serialized,
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                # Fail open: leave content untouched
                continue

        return result
