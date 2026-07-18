"""Cursor-config TypedDicts — extracted from _cursor.py for module-size compliance.

Belongs to the ``_cursor.py`` facade. Re-exported there for backward
compatibility with sibling bootstrap modules
(``_cursor_cli.py``, ``_cursor_ide.py``) that import via the parent.
"""

from __future__ import annotations

from typing_extensions import TypedDict


class CursorServerEntry(TypedDict, total=False):
    """TRW MCP server entry within Cursor's mcp.json ``mcpServers`` section."""

    command: str | list[str]
    args: list[str]


class CursorMcpConfig(TypedDict, total=False):
    """Shape of a parsed .cursor/mcp.json document."""

    mcpServers: dict[str, CursorServerEntry]


class CursorHookEntry(TypedDict, total=False):
    """Single hook entry in the *legacy* list-style .cursor/hooks.json.

    Used only by ``generate_cursor_hooks`` (the backward-compat legacy helper).
    New code should use ``HookHandlerEntry`` and ``CursorHooksV1Config``.
    """

    event: str
    command: str
    description: str


class HookHandlerEntry(TypedDict, total=False):
    """A single handler within a Cursor hooks.json v1 event list.

    Cursor v2.4+ hooks.json shape::

        {
          "version": 1,
          "hooks": {
            "<eventName>": [<HookHandlerEntry>, ...]
          }
        }

    Required key: ``command``.  Optional: ``type``, ``timeout``, ``failClosed``, ``matcher``.
    """

    command: str
    type: str
    timeout: int
    failClosed: bool
    matcher: str


class CursorHooksV1Config(TypedDict, total=False):
    """Shape of a parsed .cursor/hooks.json (version 1, dict-of-events format).

    Contrast with the legacy list-style format used by ``generate_cursor_hooks``.
    """

    version: int
    hooks: dict[str, list[HookHandlerEntry]]


class CursorHooksConfig(TypedDict, total=False):
    """Shape of a parsed .cursor/hooks.json document (legacy list style)."""

    hooks: list[CursorHookEntry]
