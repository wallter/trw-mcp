"""Friendly client names for ``trw_dispatch`` (registry data, PUBLIC, BSL-1.1).

Belongs to the ``trw_mcp.dispatch`` package, beside the :mod:`._client_specs`
registry: like it, this module is allowed to spell client ids as literals, and
nothing else in the package may.
"""

from __future__ import annotations

from trw_mcp.dispatch._client_specs import DispatchClient

#: Friendly names an agent may type for a client (``trw_dispatch(client=...)``).
CLIENT_ALIASES: dict[str, DispatchClient] = {
    "antigravity": "agy",
    "antigravity-cli": "agy",
    "cursor": "cursor-cli",
    "cursor-agent": "cursor-cli",
    "gh-copilot": "copilot",
}

#: Bare model names that imply their client (``"sonnet"`` == ``"claude:sonnet"``).
MODEL_SHORTHANDS: dict[str, tuple[DispatchClient, str]] = {
    "opus": ("claude", "opus"),
    "sonnet": ("claude", "sonnet"),
    "haiku": ("claude", "haiku"),
}
