"""Copilot distill channels — C1, C2, C3 renderers + postToolUse correlation.

# Managed by TRW — no trw_distill imports permitted.

Channels consuming PRD-DIST-2400 substrate (PRD-CORE-239 FR01 removed
the two instruction-file segments; what remains is distill-free):
- copilot-instructions-distill   (instruction_file_segment, T1 max, TIER_DOWN stale)
- copilot-path-instructions-distill (path_scoped_file, applyTo dir globs, FULL_PRUNE stale)
- copilot-vscode-mcp-config      (vscode_mcp_config, json_key_merge, 'servers' root key)
- copilot-mcp-tool-return        (mcp_tool_return, T2 default — no new code, C3-gated)

PRD-DIST-2406.
"""

from __future__ import annotations

from trw_mcp.channels.copilot._vscode_mcp import (
    generate_vscode_mcp_config as generate_vscode_mcp_config,
)

__all__ = [
    "generate_vscode_mcp_config",
]
