"""Which client claims a shared instruction surface — and cleanup when none does.

Belongs to the ``_agents_md.py`` facade. Re-exported there for back-compat.

The shared ``AGENTS.md`` is written by TRW but claimed by only *some* clients. The claim is read from the profile registry, never from
a hardcoded client name, so a profile that starts or stops declaring a surface
is honoured without a second edit. When nobody claims a surface, ceasing to
write it is not enough: whatever was already injected stays behind, frozen,
tracking a framework version that has moved on. These helpers remove it —
strictly inside the TRW markers (CONSTITUTION HB-2).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

import structlog

from trw_mcp.bootstrap._safe_remove import remove_if_hash
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "_any_client_writes_agents_md",
    "_claude_code_claimed",
    "_strip_trw_section",
    "retire_legacy_claude_md",
    "strip_orphaned_agents_md_block",
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


def _claude_code_claimed(client_ids: Sequence[str], *, from_record: bool = False) -> bool:
    """Return whether claude-code is one of this project's clients.

    An EMPTY set is the default scaffold (no client identified yet), which is
    claude-code. A DETECTED ``["cursor-ide"]`` also counts: detection reports
    cursor-ide from ``shutil.which("cursor")``, a machine-global signal, so it
    must not withhold the default client's carrier. A RECORDED list is taken at
    its word. Unknown ids resolve to the claude-code fallback profile.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    if not client_ids:
        return True
    if not from_record and list(client_ids) == ["cursor-ide"]:
        return True
    for client_id in client_ids:
        try:
            if resolve_client_profile(client_id).client_id == "claude-code":
                return True
        except Exception:  # justified: an unresolvable client cannot claim the surface
            logger.debug("claude_code_profile_unresolved", client=client_id, exc_info=True)
    return False


def _strip_trw_section(content: str) -> tuple[bool, str]:
    """Remove the TRW auto-generated block and its auto-comment."""
    # Line-anchored, never a substring scan: a marker MENTIONED in prose or
    # backticks would otherwise open the region and this function DELETES what it
    # spans. Same shape that destroyed 705 ROADMAP lines; this was the last
    # substring matcher left on the instruction-file path.
    # The start marker must stand alone on its line (or be the collapsed ``START END`` form):
    # beginning a line is not enough -- a prose line "<!-- trw:start --> is the sentinel" opened
    # the region and heal_pointer deleted the user's imports below it (HEAL-POINTER-ANCHOR).
    from trw_mcp.bootstrap._file_ops import find_marker_line_span

    start_line = re.compile(
        rf"^[ \t]*(?P<m>{re.escape(TRW_MARKER_START)})[ \t]*(?:{re.escape(TRW_MARKER_END)}[ \t]*)?\r?$",
        flags=re.MULTILINE,
    )
    start_match = start_line.search(content)
    start_span = (start_match.start("m"), start_match.end("m")) if start_match else None
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

    CORE262-14: the write goes through :class:`FileStateWriter`, not a bare
    ``path.write_text()`` -- the same shape ``heal_pointer`` already uses for
    this exact strip-only, content-preserving contract. A bare write's
    ``OSError`` handler returned ``False`` indistinguishably from the (much
    more common) "nothing to strip" case, so a genuine disk failure and a
    no-op looked identical to every caller, and both this init path's caller
    and the update path's caller merged that ``False`` into a silent no-op --
    reporting a clean result while a foreign TRW block stayed in the file.
    ``FileStateWriter.write_text`` raises ``StateError`` instead, so a write
    failure is now distinguishable from a no-op and can propagate as a loud
    refusal rather than a silently-ignored return value.
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

    from trw_mcp.state.persistence import FileStateWriter

    FileStateWriter().write_text(path, remaining.rstrip() + "\n" if remaining.strip() else "")
    logger.info("instruction_orphan_block_removed", surface=surface, path=str(path))
    return True


def strip_orphaned_agents_md_block(project_root: Path, client_ids: Sequence[str] | None = None) -> bool:
    """Remove a stale TRW block from AGENTS.md when no client here writes it.

    Restored 2026-07-29, WIRED this time. The first version of this function was
    deleted (PRD-QUAL-131-FR06) for having zero production call sites: it was
    defined, given six passing tests, and described in a commit message as
    "verified" — of a function nothing called. The tests exercised the helper
    directly, so they proved the code worked and said nothing about whether it
    ran. Deleting it was right; the NEED it addressed was still real.

    That need: PRD-CORE-240-FR04 withdrew opencode's shared AGENTS.md. Ceasing
    to write a surface does not remove what was already written, so a project
    installed BEFORE the withdrawal keeps its injected block forever — frozen,
    unowned, tracking a framework version that has moved on, and looking current.

    Called from ``_update_mcp_config`` on the update path. Its test drives
    ``update_project`` rather than this function, which is the difference
    between testing the behavior and testing the code.
    """
    # *client_ids* should be the project's resolved targets. Detection is the
    # fallback and over-claims: `detect_ide` reports the cursor clients from
    # `shutil.which("cursor")`, and both declare AGENTS.md — so on any machine
    # with Cursor installed this would decline to clean up every project.
    ids = list(client_ids) if client_ids is not None else _detect_ide(project_root)
    if _any_client_writes_agents_md(ids):
        return False
    return _strip_orphaned_block(project_root / "AGENTS.md", surface="agents_md")


#: Lines TRW itself put in a root CLAUDE.md outside its marker block: the
#: single-source pointer, imports of the two ``.trw`` sidecars TRW generated
#: before 8.0, and the placeholder scaffold
#: ``init-project`` wrote before 8.0. A file holding nothing else carries no
#: user content; any other line, including a user's own ``@.trw/...`` import,
#: keeps the file. (A file holding ONLY the pointer is the user's adapter and is kept
#: by :func:`retire_legacy_claude_md` before this test runs; uninstall still cuts it.)
_TRW_ONLY_CLAUDE_MD_LINES = frozenset(
    {
        "# CLAUDE.md",
        "@AGENTS.md",
        "@./AGENTS.md",
        "# Project Instructions",
        "This file provides guidance to AI coding clients when working with code in this repository.",
        "## What This Is",
        "{Describe your project here}",
        "## Build & Test Commands",
        "```bash",
        "# Add your project's build and test commands here",
        "```",
        "## Project Conventions",
        "{Add project-specific conventions here}",
        "@.trw/INSTRUCTIONS.md",
        "@.trw/COPILOT-INSTRUCTIONS.md",
    }
)


def _is_trw_only(content: str) -> bool:
    lines = (line.strip() for line in content.splitlines())
    return all(not line or line in _TRW_ONLY_CLAUDE_MD_LINES for line in lines)


def strip_legacy_claude_md(content: str) -> tuple[bool, str, bool]:
    """Uninstall's cut of a legacy root ``CLAUDE.md``: ``(changed, rendered, delete)``.

    TRW-only content (the test :func:`retire_legacy_claude_md` applies on update)
    deletes the file; otherwise only TRW's marker block goes and every user line stays.
    """
    had_block, remaining = _strip_trw_section(content)
    if _is_trw_only(remaining):
        return True, "", True
    return had_block, remaining, False


#: A root ``CLAUDE.md`` whose ONLY non-blank content is one of these lines imports AGENTS.md, so it does not
#: mask the carrier, and it is exactly the line the kept-warning tells users to add. It is the user's file,
#: tracked or not (FB-INSTALL-02, feedback sub_i7UMmxUbTbsdW0eD).
_ADAPTER_ONLY_CLAUDE_MD_LINES = frozenset({"@AGENTS.md", "@./AGENTS.md"})


def _is_adapter_only(content: str) -> bool:
    """True when the file's only non-blank line imports AGENTS.md (a lone shim, no TRW block or scaffold)."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return len(lines) == 1 and lines[0] in _ADAPTER_ONLY_CLAUDE_MD_LINES


def retire_legacy_claude_md(project_root: Path) -> str | None:
    """Delete a root ``CLAUDE.md`` that holds only TRW content (8.0 breaking change).

    Claude Code reads ``AGENTS.md`` natively, and reads it ONLY when no
    ``CLAUDE.md`` exists in the working directory or above it, so TRW's own
    ``CLAUDE.md`` both duplicates and masks the shared carrier.

    Returns ``"removed"`` when the file was TRW-only (the TRW marker block, the old scaffold, TRW's
    sidecar imports, those mixed with the ``@AGENTS.md`` pointer, or a symlink to ``AGENTS.md``), ``"adapter"``
    when its only content is the ``@AGENTS.md`` import -- the file does not block AGENTS.md and is the shim TRW
    itself recommends, so it is left untouched --, ``"kept"`` when it has user content -- never deleted or
    edited; the caller reports it -- and ``None`` when there is no file.
    """
    path = project_root / "CLAUDE.md"
    if path.is_symlink():
        if path.resolve() == (project_root / "AGENTS.md").resolve():
            path.unlink()
            logger.info("legacy_claude_md_removed", path=str(path), reason="symlink")
            return "removed"
        return "kept"
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "kept"
    if _is_adapter_only(content):
        logger.info("legacy_claude_md_kept_adapter", path=str(path))
        return "adapter"
    # The verdict ignores line endings (the marker match is on "\n", so a CR-only file hid TRW's block);
    # the bytes remove_if_hash verifies are still the raw ones.
    _, remaining = _strip_trw_section(content.replace("\r\n", "\n").replace("\r", "\n"))
    if not _is_trw_only(remaining):
        logger.info("legacy_claude_md_kept_user_content", path=str(path))
        return "kept"
    # The TRW-only verdict is over THESE bytes: remove_if_hash re-verifies the captured file against their hash
    # and puts it back on any change, so a line the user adds while init/update runs survives (HB-2).
    outcome = remove_if_hash(path, project_root, hashlib.sha256(raw).hexdigest(), key="CLAUDE.md")
    if outcome.status not in ("removed", "absent"):
        logger.info("legacy_claude_md_kept_changed", path=str(path), reason=outcome.reason)
        return "kept"
    logger.info("legacy_claude_md_removed", path=str(path))
    return "removed"
