"""Codex distill channels — AGENTS.md segment + PostToolUse hook.

# Managed by TRW — no trw_distill imports permitted.

Two active channels consuming PRD-DIST-2400 substrate:
- codex-agents-md-hotspots  (instruction_file_segment, T1 default)
- codex-posttooluse-telemetry (hook_script, status: active)

T2 tool-return enrichment is delivered by the shared enrich_response /
_tool_return_tiers substrate path (client_tier=T2 resolved from
TRW_CLIENT_PROFILE env var) — there is no codex-specific per-client
builder. See channels/_tool_return_tiers.py.

PRD-DIST-2402.
"""

from __future__ import annotations

from trw_mcp.channels.codex._agents_hotspots import (
    build_codex_channel_entry as build_codex_channel_entry,
)
from trw_mcp.channels.codex._agents_hotspots import (
    render_and_inject as render_and_inject,
)

__all__ = [
    "build_codex_channel_entry",
    "render_and_inject",
]
