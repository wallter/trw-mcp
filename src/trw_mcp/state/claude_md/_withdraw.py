"""Withdraw learnings already written to REVIEW.md when recall is off.

The readers stop new learnings reaching REVIEW.md, but a file written while
recall was on still carries them, and an arm that switches recall off would read
them there. Session start calls this, so the switch takes effect in the file at
the moment it takes effect in recall. REVIEW.md is regenerated whole.

AGENTS.md is not touched: it carries no learnings since PRD-CORE-341, and the
next instruction sync replaces a legacy block (Key Learnings included) with the
link to ``.trw/INSTRUCTIONS.md``.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._recall_gate import learnings_injection_allowed


def withdraw_managed_learnings(trw_dir: Path, project_root: Path, config: TRWConfig) -> list[str]:
    """Regenerate REVIEW.md without learnings when ``config`` turns recall off; return the files changed."""
    if learnings_injection_allowed(config, "passive"):
        return []
    if not (project_root / "REVIEW.md").is_file():
        return []
    from trw_mcp.state.claude_md._sync import generate_review_md

    generate_review_md(
        trw_dir, repo_root=project_root, allow_empty=True
    )  # recall is off: dropping the learnings is the point
    return [str(project_root / "REVIEW.md")]
