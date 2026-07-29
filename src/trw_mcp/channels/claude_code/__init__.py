"""Claude Code distill channels (PRD-DIST-2405).

Five channels wired to the Claude Code client surfaces:
- CC-01: MEMORY.md distill snapshot (``write_distill_snapshot``)
- CC-02: CLAUDE.md distill segment (``install_cc02_segment``, ``update_cc02_segment``)
- CC-03: PreToolUse edit hint hook (``pre-tool-distill-hint.sh``)
- CC-04: PostToolUse correlation (``post-tool-event.sh`` extension)
- CC-05: trw-distill-explorer subagent (``install_cc05_subagent``)

Re-exports the compute function for the CC-03 hook script:
  ``from trw_mcp.channels.claude_code import compute_before_edit_hint``

Zero trw_distill imports permitted in this package (IP boundary).
"""

from __future__ import annotations

from trw_mcp.channels.claude_code._explorer_subagent import (
    install_cc05_subagent as install_cc05_subagent,
)

# Re-export the standalone compute function (P0-09 canonical re-export path)
from trw_mcp.tools.before_edit_hint import (
    compute_before_edit_hint as compute_before_edit_hint,
)

__all__ = [
    "compute_before_edit_hint",
    "install_cc05_subagent",
]
