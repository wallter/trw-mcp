"""Copilot distill channels — C1, C2, C3 renderers + postToolUse correlation.

# Managed by TRW — no trw_distill imports permitted.

Four channels consuming PRD-DIST-2400 substrate:
- copilot-instructions-distill   (instruction_file_segment, T1 max, TIER_DOWN stale)
- copilot-path-instructions-distill (path_scoped_file, applyTo dir globs, FULL_PRUNE stale)
- copilot-vscode-mcp-config      (vscode_mcp_config, json_key_merge, 'servers' root key)
- copilot-mcp-tool-return        (mcp_tool_return, T2 default — no new code, C3-gated)

PRD-DIST-2406.
"""

from __future__ import annotations

from trw_mcp.channels.copilot._instructions_distill import (
    CopilotInstructionsDistillRenderer as CopilotInstructionsDistillRenderer,
)
from trw_mcp.channels.copilot._instructions_distill import (
    build_copilot_instructions_distill_entry as build_copilot_instructions_distill_entry,
)
from trw_mcp.channels.copilot._path_instructions import (
    CopilotPathInstructionsRenderer as CopilotPathInstructionsRenderer,
)
from trw_mcp.channels.copilot._path_instructions import (
    build_copilot_path_instructions_entry as build_copilot_path_instructions_entry,
)
from trw_mcp.channels.copilot._path_instructions import (
    compute_apply_to_glob as compute_apply_to_glob,
)
from trw_mcp.channels.copilot._vscode_mcp import (
    generate_vscode_mcp_config as generate_vscode_mcp_config,
)

__all__ = [
    "CopilotInstructionsDistillRenderer",
    "CopilotPathInstructionsRenderer",
    "build_copilot_instructions_distill_entry",
    "build_copilot_path_instructions_entry",
    "compute_apply_to_glob",
    "generate_vscode_mcp_config",
]
