"""Codex distill channels — AGENTS.md segment + T2 tool-return + PostToolUse hook.

# Managed by TRW — no trw_distill imports permitted.

Three channels consuming PRD-DIST-2400 substrate:
- codex-agents-md-hotspots  (instruction_file_segment, T1 default)
- codex-tool-return-t2      (mcp_tool_return, T2 default for codex)
- codex-posttooluse-telemetry (hook_script, status: aspirational)

PRD-DIST-2402.
"""

from __future__ import annotations

from trw_mcp.channels.codex._agents_hotspots import (
    build_codex_channel_entry as build_codex_channel_entry,
)
from trw_mcp.channels.codex._agents_hotspots import (
    render_and_inject as render_and_inject,
)
from trw_mcp.channels.codex._tool_return_t2 import (
    build_t2_payload as build_t2_payload,
)
from trw_mcp.channels.codex._tool_return_t2 import (
    get_default_tier_for_codex as get_default_tier_for_codex,
)

__all__ = [
    "build_codex_channel_entry",
    "build_t2_payload",
    "get_default_tier_for_codex",
    "render_and_inject",
]
