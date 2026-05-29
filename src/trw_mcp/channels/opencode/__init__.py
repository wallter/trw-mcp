"""opencode distill channels — AGENTS.md segment + custom commands + explorer subagent.

# Managed by TRW — no trw_distill imports permitted.

Six channels consuming PRD-DIST-2400 substrate:
- opencode-agents-md-segment        (instruction_file_segment, T1 default)
- opencode-custom-cmd-before-edit   (custom_command, FULL_REWRITE)
- opencode-custom-cmd-hotspots      (custom_command, FULL_REWRITE)
- opencode-custom-cmd-conventions   (custom_command, FULL_REWRITE)
- opencode-tool-return-enrichment   (mcp_tool_return, T2 default per P1-11)
- opencode-explorer-agent           (subagent_file, FULL_REWRITE)

T2 tool-return payload construction uses the shared substrate
``channels/_tool_return_tiers.py::enrich_response()`` called directly
from the three distill tool files — no per-client builder in this package.

PRD-DIST-2403.
"""

from __future__ import annotations

from trw_mcp.channels.opencode._agents_md_segment import (
    install_opencode_agents_md_distill_segment as install_opencode_agents_md_distill_segment,
)
from trw_mcp.channels.opencode._custom_commands import (
    install_custom_commands as install_custom_commands,
)
from trw_mcp.channels.opencode._explorer_agent import (
    install_explorer_agent as install_explorer_agent,
)
from trw_mcp.channels.opencode._ip_filter import (
    filter_proprietary_paths as filter_proprietary_paths,
)
from trw_mcp.channels.opencode._shared_lock import (
    agents_md_lock as agents_md_lock,
)
from trw_mcp.channels.opencode._tool_return_enrichment import (
    get_default_tier_for_opencode as get_default_tier_for_opencode,
)
from trw_mcp.channels.opencode._tool_return_enrichment import (
    is_opencode_client as is_opencode_client,
)
from trw_mcp.channels.opencode._tool_return_enrichment import (
    resolve_transport as resolve_transport,
)

__all__ = [
    "agents_md_lock",
    "filter_proprietary_paths",
    "get_default_tier_for_opencode",
    "install_custom_commands",
    "install_explorer_agent",
    "install_opencode_agents_md_distill_segment",
    "is_opencode_client",
    "resolve_transport",
]
