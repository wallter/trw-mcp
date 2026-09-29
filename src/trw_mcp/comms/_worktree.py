"""The member-bound writer of an FR17 worktree membership record (lane B's helper path).

Belongs to the ``trw_mcp.comms`` facade; re-exported there.

FR17 names two writers of the record that lets a linked worktree use the main
root: the orchestrator's admission of a worktree candidate (FR18) and "the lane-B
worktree helper while its member was bound under FR01 from the main root". This
is the second. The caller proves who it is by binding at the MAIN root, and it can
record only ITSELF, for a worktree git links to that same main root. Nothing the
caller passes names a member, a formation or a revision.
"""

from __future__ import annotations

__all__ = []
