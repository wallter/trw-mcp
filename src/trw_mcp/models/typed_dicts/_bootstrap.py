"""Bootstrap TypedDicts for IDE configuration generation functions.

Covers the return shapes of:
- generate_opencode_config()   (_opencode.py)
- generate_agents_md()         (_opencode.py)
- generate_cursor_hooks()      (_cursor.py)
- generate_cursor_rules()      (_cursor.py)
- generate_cursor_mcp_config() (_cursor.py)
"""

from __future__ import annotations

from typing_extensions import TypedDict

from trw_mcp.models.typed_dicts._opencode import (
    OpencodeConfig,
    OpencodeServerEntry,
    OpencodeTemplateDict,
)

__all__ = [
    "BootstrapFileResult",
    "OpencodeConfig",
    "OpencodeServerEntry",
    "OpencodeTemplateDict",
]


class BootstrapFileResult(TypedDict, total=False):
    """Return shape of bootstrap config-generation functions.

    All five public functions in ``_opencode.py`` and ``_cursor.py`` return
    this shape.  ``errors`` is only populated by the opencode functions;
    cursor functions never set it, so the key is absent when not applicable.
    ``total=False`` because cursor functions omit ``errors`` entirely.

    ``info`` is used by cursor-cli to surface TTY/tmux reminders and other
    advisory messages that are not errors or warnings (PRD-CORE-137-FR08a).
    """

    created: list[str]
    updated: list[str]
    preserved: list[str]
    errors: list[str]
    info: list[str]
