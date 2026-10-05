"""Antigravity CLI distill channels — ANTIGRAVITY.md segment + explorer subagent.

# Managed by TRW — no trw_distill imports permitted.

Three channels consuming PRD-DIST-2400 substrate:
- ag-01-antigravity-md-distill        (instruction_file_segment, T1 default, status: active)
- ag-02-distill-explorer-subagent     (subagent_file, T1 default per audit P1-15, status: active)
- ag-03-before-edit-hook              (hook_script, status: active — registered as a named PreToolUse
                                       hook in .agents/hooks.json, which agy 1.2.x reads; verified
                                       live on agy 1.2.15: it fires for write_to_file)

AG-04 tool-return enrichment is delivered by the shared enrich_response /
_tool_return_tiers substrate path (client_tier resolved from TRW_CLIENT_PROFILE
env var). There is no per-client AG-04 builder; the generic __tool_call__ channel
carries the ``client`` field so meta-tune can segment by client. See
channels/_tool_return_tiers.py.

PRD-DIST-2404.
"""

from __future__ import annotations

from trw_mcp.channels.antigravity._before_edit_hook import (
    AG03_HOOKS_PATH as AG03_HOOKS_PATH,
)
from trw_mcp.channels.antigravity._before_edit_hook import (
    HOOK_SCRIPT_CONTENT as HOOK_SCRIPT_CONTENT,
)
from trw_mcp.channels.antigravity._before_edit_hook import (
    generate_hook_script as generate_hook_script,
)
from trw_mcp.channels.antigravity._before_edit_hook import (
    install_before_edit_hook as install_before_edit_hook,
)
from trw_mcp.channels.antigravity._explorer_subagent import (
    AG02_CHANNEL_ID as AG02_CHANNEL_ID,
)
from trw_mcp.channels.antigravity._explorer_subagent import (
    AgentWriteResult as AgentWriteResult,
)
from trw_mcp.channels.antigravity._explorer_subagent import (
    generate_distill_explorer_agent as generate_distill_explorer_agent,
)

__all__ = [
    "AG02_CHANNEL_ID",
    "AG03_HOOKS_PATH",
    "HOOK_SCRIPT_CONTENT",
    "AgentWriteResult",
    "generate_distill_explorer_agent",
    "generate_hook_script",
    "install_before_edit_hook",
]
