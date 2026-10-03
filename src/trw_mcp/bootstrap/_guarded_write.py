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
    expected_current: str | None = None,
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
        expected_current: The text *content* was built from; a file that changed since is left as found.

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
        expected_current=expected_current,
    )
    if verdict.written:
        _record_write(result, rel_path, existed=existed)
        name_kept_copy(result, rel_path, verdict.backup_path, markers)
        return True

    refusal = verdict.refusal
    detail = refusal["detail"] if refusal is not None else "guard declined the write"
    reason = refusal["reason"] if refusal is not None else "unknown"
    from trw_mcp.state.claude_md._marker_layout import refusal_message

    result.setdefault("errors", []).append(refusal_message(f"Refused to write {target} ({reason}): {detail}", reason))
    logger.warning("instruction_bootstrap_write_refused", path=str(target), reason=reason)
    return False


def name_kept_copy(result: dict[str, list[str]], rel_path: str, backup: str | None, markers: tuple[str, str]) -> None:
    """Name the previous version's backup when it held the user's own text (CANARY-ACCEPT dev22 P0).

    The guard backs a file up before every change, but a backup nobody is told about is not a copy the user can
    find, and an uncommitted file has no other. A previous version that held only TRW's block is not named: it is
    TRW's own text, and naming it would add a line to every upgrade. An unreadable backup is named, to be safe.
    """
    if backup is None:
        return
    from trw_mcp.state.claude_md._write_measure import non_generated_bytes

    try:
        if not non_generated_bytes(Path(backup).read_text(encoding="utf-8"), markers):
            return
    except (OSError, ValueError):  # trw-fail-silent-allow: unreadable or not UTF-8 -> named below, never hidden
        pass
    result.setdefault("warnings", []).append(
        f"{rel_path}: TRW updated its block; your previous version is kept at {Path(backup).absolute()} "
        "(delete it when satisfied)"
    )


__all__ = ["guarded_bootstrap_write", "name_kept_copy"]
