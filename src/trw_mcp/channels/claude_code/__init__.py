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

from trw_mcp.channels.claude_code._cc02_segment import (
    install_cc02_segment as install_cc02_segment,
)
from trw_mcp.channels.claude_code._cc02_segment import (
    render_cc02_segment as render_cc02_segment,
)
from trw_mcp.channels.claude_code._cc02_segment import (
    update_cc02_segment as update_cc02_segment,
)
from trw_mcp.channels.claude_code._explorer_subagent import (
    install_cc05_subagent as install_cc05_subagent,
)
from trw_mcp.channels.claude_code._memory_path import (
    derive_claude_project_id as derive_claude_project_id,
)
from trw_mcp.channels.claude_code._memory_path import (
    resolve_memory_dir as resolve_memory_dir,
)
from trw_mcp.channels.claude_code._memory_writer import (
    write_distill_snapshot as write_distill_snapshot,
)

# Re-export the standalone compute function (P0-09 canonical re-export path)
from trw_mcp.tools.before_edit_hint import (
    compute_before_edit_hint as compute_before_edit_hint,
)

__all__ = [
    "compute_before_edit_hint",
    "derive_claude_project_id",
    "install_cc02_segment",
    "install_cc05_subagent",
    "render_cc02_segment",
    "resolve_memory_dir",
    "update_cc02_segment",
    "write_distill_snapshot",
]
