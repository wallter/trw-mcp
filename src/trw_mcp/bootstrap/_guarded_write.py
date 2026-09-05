"""Bootstrap adapter for the instruction write guard (PRD-FIX-123-FR06).

Belongs to the ``bootstrap`` package. The bootstrap writers of a repo-root
``CLAUDE.md`` / ``AGENTS.md`` used five bare, non-atomic ``Path.write_text``
calls — two of which replaced a user's file wholesale and reported ``created``.
They all route through :func:`guarded_bootstrap_write` now, which is a thin
translation of :func:`guarded_instruction_write`'s verdict into the bootstrap
``{created, updated, preserved, errors}`` result shape.

This module is the ONLY sanctioned indirection to the guard for bootstrap code.
The FR06 totality test asserts both halves: every enumerated writer reaches the
guard through this helper or directly, and this helper itself calls the guard —
a name-based registry check is otherwise blind inside its own member.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._file_ops import _record_write

logger = structlog.get_logger(__name__)


def guarded_bootstrap_write(
    target: Path,
    content: str,
    *,
    project_root: Path,
    markers: tuple[str, str],
    result: dict[str, list[str]],
    rel_path: str,
    force: bool = False,
    enforce_shrink_floor: bool = True,
) -> bool:
    """Write *content* to *target* through the guard, recording the outcome.

    Args:
        target: Instruction file to write.
        content: Full candidate content.
        project_root: Root that contains the backup directory.
        markers: The caller's marker dialect. Every writer targets
            ``<!-- trw:start -->`` / ``<!-- trw:end -->`` now (PRD-CORE-243-
            FR06/FR08 retired the cursor-cli-only ``<!-- TRW:BEGIN -->`` pair);
            that legacy pair still works as a *migration* dialect only —
            ``_write_measure.py`` recognises it unconditionally so a file
            mid-migration never has its dead legacy block read as user-content
            loss.
        result: Bootstrap result payload to record into.
        rel_path: Label recorded under ``created`` / ``updated`` / ``errors``.
        force: Bypass the shrink floors (the installer's ``--force``).
        enforce_shrink_floor: ``False`` for wholly TRW-owned managed files.

    Returns:
        Whether the bytes landed. A refusal leaves *target* byte-identical and
        appends a message naming the reason to ``result["errors"]``.
    """
    from trw_mcp.state.claude_md._write_guard import guarded_instruction_write

    existed = target.exists()
    verdict = guarded_instruction_write(
        target,
        content,
        markers=markers,
        force=force,
        enforce_shrink_floor=enforce_shrink_floor,
        project_root=project_root,
    )
    if verdict.written:
        _record_write(result, rel_path, existed=existed)
        return True

    refusal = verdict.refusal
    detail = refusal["detail"] if refusal is not None else "guard declined the write"
    reason = refusal["reason"] if refusal is not None else "unknown"
    result.setdefault("errors", []).append(f"Refused to write {target} ({reason}): {detail}")
    logger.warning("instruction_bootstrap_write_refused", path=str(target), reason=reason)
    return False


def guarded_claude_md_scaffold_write(
    claude_md_path: Path,
    content: str,
    *,
    project_root: Path,
    result: dict[str, list[str]],
    force: bool,
) -> bool:
    """Route a forced CLAUDE.md scaffold rewrite through the guard.

    `_minimal_claude_md()` already embeds the standard trw:start/trw:end
    markers, so this is a thin ``guarded_bootstrap_write`` call naming them --
    kept here (rather than inline in ``_init_project.py``) to stay under the
    350-eLOC gate and to keep every CLAUDE.md/AGENTS.md write behind this one
    sanctioned indirection.
    """
    from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

    return guarded_bootstrap_write(
        claude_md_path,
        content,
        project_root=project_root,
        markers=(TRW_MARKER_START, TRW_MARKER_END),
        result=result,
        rel_path="CLAUDE.md",
        force=force,
    )


__all__ = ["guarded_bootstrap_write", "guarded_claude_md_scaffold_write"]
