"""Withdraw learnings already written to AGENTS.md and REVIEW.md when recall is off.

The readers stop new learnings reaching these files, but a file written while
recall was on still carries them, and an arm that switches recall off would read
them there. Session start calls this, so the switch takes effect in the files at
the moment it takes effect in recall. Only TRW's managed content changes: the
learnings block inside the AGENTS.md markers, and REVIEW.md, which TRW
regenerates whole.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._recall_gate import learnings_injection_allowed
from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START, _marker_line_index

_LEARNINGS_HEADING = "## Key Learnings"


def withdraw_managed_learnings(trw_dir: Path, project_root: Path, config: TRWConfig) -> list[str]:
    """Remove written learnings when ``config`` turns recall off; return the files changed."""
    if learnings_injection_allowed(config, "passive"):
        return []
    changed: list[str] = []
    agents = project_root / "AGENTS.md"
    if agents.is_file():
        # Whole-line matches only (.claude/rules/trw-mcp-python.md §Marker /
        # Sentinel Matching): prose that mentions a marker is not the block.
        lines = agents.read_text(encoding="utf-8").splitlines(keepends=True)
        start = _marker_line_index(lines, TRW_MARKER_START)
        end = None if start is None else _marker_line_index(lines, TRW_MARKER_END, after=start)
        block = None if end is None else _marker_line_index(lines[:end], _LEARNINGS_HEADING, after=start)
        if block is not None and end is not None:
            # Narrow, marker-scoped removal of the learnings sub-block only --
            # never a content REPLACEMENT of the whole file -- so this cannot
            # route through `guarded_instruction_write`: that seam refuses any
            # write that shrinks the file without `force=True`, and forcing it
            # would disable the shrink-floor safety net for this call entirely
            # rather than express what actually happens (structurally
            # identical to `_orphan_strip._strip_orphaned_block`, which is
            # allowlisted for the same reason). Uses `FileStateWriter`, the
            # same seam `_strip_orphaned_block`/`heal_pointer` use, so a
            # genuine write failure raises `StateError` instead of a bare
            # `OSError` disappearing silently.
            from trw_mcp.state.persistence import FileStateWriter

            FileStateWriter().write_text(agents, "".join(lines[:block] + lines[end:]))
            changed.append(str(agents))
    if (project_root / "REVIEW.md").is_file():
        from trw_mcp.state.claude_md._sync import generate_review_md

        generate_review_md(trw_dir, repo_root=project_root)
        changed.append(str(project_root / "REVIEW.md"))
    return changed
