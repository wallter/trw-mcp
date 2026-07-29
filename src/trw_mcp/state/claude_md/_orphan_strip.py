"""Which client claims a shared instruction surface — and cleanup when none does.

Belongs to the ``_agents_md.py`` facade. Re-exported there for back-compat.

Two shared files (``AGENTS.md``, ``CLAUDE.md``) are written by TRW but claimed
by only *some* clients. The claim is read from the profile registry, never from
a hardcoded client name, so a profile that starts or stops declaring a surface
is honoured without a second edit. When nobody claims a surface, ceasing to
write it is not enough: whatever was already injected stays behind, frozen,
tracking a framework version that has moved on. These helpers remove it —
strictly inside the TRW markers (CONSTITUTION HB-2).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import structlog

from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "_any_client_writes_agents_md",
    "_any_client_writes_claude_md",
    "_strip_trw_section",
    "strip_orphaned_claude_md_block",
]


def _detect_ide(project_root: Path) -> list[str]:
    """Detect through the ``_agents_md`` facade so test patches there propagate.

    Binding ``detect_ide`` from ``_utils`` directly would make these helpers
    invisible to ``patch.object(_agents_md, "detect_ide", ...)`` — and a helper
    that silently ignores the patched client list still DELETES a block.
    """
    from trw_mcp.state.claude_md import _agents_md

    return _agents_md.detect_ide(project_root)


def _any_client_writes_agents_md(client_ids: Sequence[str]) -> bool:
    """Return whether ANY detected client declares the shared AGENTS.md."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    for client_id in client_ids:
        try:
            if resolve_client_profile(client_id).write_targets.agents_md:
                return True
        except Exception:  # justified: an unresolvable client cannot claim the surface
            logger.debug("agents_md_surface_profile_unresolved", client=client_id, exc_info=True)
    return False


def _any_client_writes_claude_md(client_ids: Sequence[str], *, from_record: bool = False) -> bool:
    """Return whether CLAUDE.md is a claimed surface for this set of clients.

    An EMPTY set is the default scaffold (no client identified yet — write it),
    not an unclaimed surface.

    cursor-ide is carved out too, and this is now a REDUNDANCY rather than a
    necessity: its ``.cursor/rules/trw-ceremony.mdc`` is ``alwaysApply: true``
    and carries the full protocol including the deliver gate, so the CLAUDE.md
    block is a second copy.

    Removing it needs one thing this codebase cannot express yet. The decision
    turns on whether cursor-ide is here because the USER chose it or because
    ``detect_ide`` saw ``shutil.which("cursor")`` — and ``target_platforms`` is
    written from resolved targets at install, so it records both the same way
    (``TestInitTargetPlatforms`` pins that it must keep recording detected ones).
    Withdrawing the carve-out on the record alone therefore strips CLAUDE.md from
    any project merely installed on a machine that has Cursor. Separating the two
    is a config-schema change: record provenance alongside the client list.

    *from_record* is threaded and accurate for callers that need it; it
    deliberately does NOT gate this carve-out until that distinction exists.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    if not client_ids or list(client_ids) == ["cursor-ide"]:
        return True
    for client_id in client_ids:
        try:
            if resolve_client_profile(client_id).write_targets.claude_md:
                return True
        except Exception:  # justified: an unresolvable client cannot claim the surface
            logger.debug("claude_md_surface_profile_unresolved", client=client_id, exc_info=True)
    return False


def _strip_trw_section(content: str) -> tuple[bool, str]:
    """Remove the TRW auto-generated block and its auto-comment."""
    # Line-anchored, never a substring scan: a marker MENTIONED in prose or
    # backticks would otherwise open the region and this function DELETES what it
    # spans. Same shape that destroyed 705 ROADMAP lines; this was the last
    # substring matcher left on the instruction-file path.
    from trw_mcp.bootstrap._file_ops import find_marker_line_span

    start_span = find_marker_line_span(content, TRW_MARKER_START, anchor="start")
    end_span = find_marker_line_span(content, TRW_MARKER_END, anchor="end")
    if start_span is None or end_span is None or end_span[1] <= start_span[0]:
        return False, content
    start_idx, end_idx = start_span[0], end_span[0]

    remove_start = start_idx
    auto_comment_idx = content.rfind(TRW_AUTO_COMMENT, 0, start_idx)
    if auto_comment_idx != -1:
        between = content[auto_comment_idx + len(TRW_AUTO_COMMENT) : start_idx]
        if between.strip() == "":
            remove_start = auto_comment_idx

    remove_end = end_idx + len(TRW_MARKER_END)
    while remove_end < len(content) and content[remove_end] == "\n":
        remove_end += 1

    return True, content[:remove_start] + content[remove_end:]


def _strip_orphaned_block(path: Path, *, surface: str) -> bool:
    """Remove the TRW-marked region from an instruction file TRW no longer owns.

    Only ever removes what lies between the markers (CONSTITUTION HB-2); user
    content outside them is untouched, and a file with no block is left alone.
    """
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    stripped, remaining = _strip_trw_section(content)
    if not stripped:
        return False

    try:
        path.write_text(remaining.rstrip() + "\n" if remaining.strip() else "", encoding="utf-8")
    except OSError:
        logger.warning("instruction_orphan_strip_failed", surface=surface, path=str(path), exc_info=True)
        return False
    logger.info("instruction_orphan_block_removed", surface=surface, path=str(path))
    return True


# ``strip_orphaned_agents_md_block`` was removed 2026-07-28 (PRD-QUAL-131-FR06).
# It had ZERO production call sites: nothing in trw_mcp ever invoked it, so the
# AGENTS.md orphan-strip it described never ran. Its commit message claimed
# "Verified: opencode-only detection strips and is idempotent" -- a property
# verified of a function nothing calls, which is why its six passing tests were
# never evidence that the behavior existed. The CLAUDE.md sibling below IS
# called (from bootstrap/_template_updater.py and bootstrap/_init_project.py)
# and is the control that proves the census grep works.


def strip_orphaned_claude_md_block(project_root: Path, client_ids: Sequence[str] | None = None) -> bool:
    """Remove a stale TRW block from CLAUDE.md when no client here reads it.

    Ceasing to write a surface is not the same as removing what was already
    written: whatever TRW injected stays behind, frozen, tracking a framework
    version that has moved on. Only claude-code declares CLAUDE.md, yet bootstrap scaffolded
    the protocol into it for EVERY client — so a codex or opencode project
    carried a third copy of the framework text in a file none of its clients
    load, which then froze while the surfaces they do read moved on.

    *client_ids* should be the resolved install targets; detection is the
    fallback and is unreliable post-install (installing creates ``.claude/``).
    """
    # Provenance travels with the list, exactly as in `claude_md_is_claimed`.
    # Without it this check applied the detected-only cursor-ide carve-out to an
    # explicitly supplied install selection, so the caller decided "not claimed"
    # and then this function silently declined to act on that decision.
    ids = list(client_ids) if client_ids is not None else _detect_ide(project_root)
    if _any_client_writes_claude_md(ids, from_record=client_ids is not None):
        return False
    return _strip_orphaned_block(project_root / "CLAUDE.md", surface="claude_md")
