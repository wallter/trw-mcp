"""Retire the ``.trw`` instruction sidecars (PRD-QUAL-143-FR01).

Belongs to the ``state/claude_md`` package. The TRW block used to be moved into
``.trw/INSTRUCTIONS.md`` (Copilot: ``.trw/COPILOT-INSTRUCTIONS.md``) behind an
``@`` import. The block is inline again, so sync and ``update-project`` call
:func:`retire_instruction_sidecars` to remove every import line, inside or
outside the markers, and then set the sidecar files aside. Only a sidecar the
old generator wrote (its header) is retired, and it is renamed to
``<path>.retired`` rather than deleted, since a user may have added notes below
the header. A user-authored file at either path keeps both the file and its import.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

logger = structlog.get_logger(__name__)

SIDECAR_RELPATHS: tuple[str, ...] = (".trw/INSTRUCTIONS.md", ".trw/COPILOT-INSTRUCTIONS.md")
#: The first line the removed generator wrote into every sidecar.
_GENERATED_HEADER = "<!-- TRW AUTO-GENERATED \u2014 do not edit."
_IMPORTING_FILES: dict[str, tuple[str, str]] = {
    "CLAUDE.md": (TRW_MARKER_START, TRW_MARKER_END),
    "AGENTS.md": (TRW_MARKER_START, TRW_MARKER_END),
    ".github/copilot-instructions.md": ("<!-- trw:copilot:start -->", "<!-- trw:copilot:end -->"),
}


def _is_generated(sidecar: Path) -> bool:
    with sidecar.open(encoding="utf-8", errors="replace") as handle:
        return handle.readline().startswith(_GENERATED_HEADER)


def _set_aside(sidecar: Path) -> Path:
    """Link *sidecar* to ``<name>.retired`` (timestamped if taken), then drop its old name.

    ``os.link`` refuses any existing entry, a dangling symlink included, so an
    earlier retirement is never overwritten; the bytes stay under the new name.
    """
    candidates = [sidecar.with_name(f"{sidecar.name}.retired")]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidates += [sidecar.with_name(f"{sidecar.name}.retired-{stamp}{f'-{n}' if n else ''}") for n in range(8)]
    for kept_at in candidates:
        try:
            os.link(sidecar, kept_at, follow_symlinks=False)
        except FileExistsError:  # trw-fail-silent-allow: a taken name is expected; try the next one
            continue
        sidecar.unlink()
        return kept_at
    raise FileExistsError(f"no free .retired name for {sidecar}")


def strip_sidecar_imports(content: str, import_lines: frozenset[str]) -> str:
    """Return *content* without *import_lines*; unchanged when there are none."""
    lines = content.split("\n")
    kept: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() not in import_lines:
            kept.append(line)
            continue
        # Drop one of the two blank lines the removed import sat between.
        next_blank = index + 1 >= len(lines) or not lines[index + 1].strip()
        if kept and not kept[-1].strip() and next_blank:
            kept.pop()
    if len(kept) == len(lines):
        return content
    return "\n".join(kept).rstrip("\n") + "\n"


def retire_instruction_sidecars(project_root: Path, *, dry_run: bool = False) -> list[str]:
    """Strip sidecar imports from the instruction files, then set the sidecars aside.

    A sidecar is renamed only once no import of it survives, so a refused write
    never leaves a dangling ``@`` line. Returns the project-relative paths changed.
    """
    from trw_mcp.state.claude_md._write_guard import guarded_instruction_write

    changed: list[str] = []
    retirable: list[str] = []
    for rel in SIDECAR_RELPATHS:
        sidecar = project_root / rel
        if sidecar.is_file() and not _is_generated(sidecar):
            logger.info("sidecar_kept", path=rel, reason="not_generated")
        else:
            retirable.append(rel)
    import_lines = frozenset(f"@{rel}" for rel in retirable)
    surviving: set[str] = set()
    for rel, markers in _IMPORTING_FILES.items():
        target = project_root / rel
        if not target.is_file():
            continue
        content = target.read_text(encoding="utf-8")
        stripped = strip_sidecar_imports(content, import_lines)
        if stripped == content:
            continue
        # trw:intentional the candidate differs only by TRW's own import lines, so the
        # shrink floors (which protect hand-written text) have nothing to protect.
        verdict = guarded_instruction_write(
            target, stripped, markers=markers, force=True, dry_run=dry_run, project_root=project_root
        )
        if verdict.written:
            changed.append(rel)
        else:
            surviving.update(line.strip() for line in content.splitlines() if line.strip() in import_lines)

    if dry_run:
        return changed
    for rel in retirable:
        sidecar = project_root / rel
        if sidecar.is_file() and f"@{rel}" not in surviving:
            kept_at = _set_aside(sidecar)
            logger.info("sidecar_retired", path=rel, kept_at=kept_at.relative_to(project_root).as_posix())
            changed.append(rel)
    if changed:
        logger.info("instruction_sidecars_retired", paths=changed)
    return changed
