"""Cursor distill channels — no per-client modules remain.

PRD-CORE-239 FR01 removed all four cursor renderers (the three `cursor-mdc-*`
MDC emitters and `cursor-cli-agents-md-snapshot`). The two cursor channels that
survive are delivered without any code in this package:

- `cursor-mcp-tool-return` — the shared `_tool_return_tiers` substrate
- `cursor-pretooluse-hint` — the shipped hook `data/hooks/cursor/trw-before-edit-hint.sh`,
  which imports `channels/claude_code/_hook_helpers.py` directly

The package is kept rather than deleted because the manifest still names
`client: cursor-ide` / `cursor-cli` entries and callers may reasonably import
this path; an empty package is a truthful statement that there is nothing
cursor-specific left to import.
"""

from __future__ import annotations

__all__: list[str] = []
