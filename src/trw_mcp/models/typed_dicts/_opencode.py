"""TypedDicts for opencode.json configuration structures.

These cover the JSON shapes produced and consumed by
``trw_mcp.bootstrap._opencode``.
"""

from __future__ import annotations

from typing_extensions import TypedDict


class OpencodeServerEntry(TypedDict, total=False):
    """TRW MCP server entry within opencode.json's ``mcp`` section."""

    type: str
    command: list[str]
    args: list[str]
    enabled: bool


class OpencodeConfig(TypedDict, total=False):
    """Shape of a parsed opencode.json config document."""

    mcp: dict[str, OpencodeServerEntry]
    permission: dict[str, str]
    instructions: list[str]
    tools: dict[str, bool]
    model: str
    small_model: str
    agent: str


# Functional TypedDict form required because ``$schema`` is not a valid
# Python identifier and cannot be expressed in class-based TypedDict syntax.
OpencodeTemplateDict = TypedDict(
    "OpencodeTemplateDict",
    {
        "$schema": str,
        "instructions": list[str],
        "permission": dict[str, str],
        "tools": dict[str, bool],
        "mcp": dict[str, OpencodeServerEntry],
    },
    total=False,
)
