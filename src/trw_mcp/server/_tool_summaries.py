"""Put each tool's one summary at the head of its served description (PRD-INFRA-195-FR01).

FastMCP serves a tool's docstring (above ``Args:``) as its description. The
docstrings carry the ``Use when`` trigger and the output contract
(PRD-QUAL-074) but no summary line: the summary lives once, in
``models/tool_summaries.TOOL_SUMMARIES``, which the instruction renderer and
the docs generator also read. This step runs once at boot, after registration,
and makes every registered tool's served description
``<summary>\\n\\n<docstring body>``, so the model, the instruction files and
``/docs/tools`` read one string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trw_mcp.models.tool_summaries import TOOL_SUMMARIES

if TYPE_CHECKING:
    from fastmcp import FastMCP


def served_description(summary: str, body: str | None) -> str:
    """The description a client receives: the summary, then the docstring body if any."""
    body = (body or "").strip()
    return f"{summary}\n\n{body}" if body else summary


async def apply_tool_summaries(server: FastMCP) -> tuple[str, ...]:
    """Prefix every summarized tool's registered description with its summary; return the names.

    Mutates the registry singletons ``get_tool`` returns, exactly once at boot
    (the pattern ``_always_load.apply_always_load_meta`` uses). Idempotent: a
    description that already starts with its summary is left alone. Fails loud:
    the instruction manifest already checks at import that the summaries and
    the registered surface are the same set, so a name that does not resolve
    here is a registration bug, not a runtime condition to absorb.
    """
    applied: list[str] = []
    for name, summary in TOOL_SUMMARIES.items():
        tool: Any = await server.get_tool(name)
        if tool is None:
            raise LookupError(f"{name} has a summary but is not registered")
        current = str(tool.description or "")
        if not current.startswith(summary):
            tool.description = served_description(summary, current)
        applied.append(name)
    return tuple(applied)


__all__ = ["apply_tool_summaries", "served_description"]
