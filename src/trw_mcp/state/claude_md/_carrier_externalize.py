"""Sidecar externalization for the instruction carrier (PRD-CORE-203 FR05).

Belongs to the ``_instruction_carrier.py`` facade, which re-exports every public
symbol here so import paths and test monkeypatch seams are unchanged. Split out
to keep the facade under the 350-line ceiling enforced by
``tests/test_module_loc_gate.py``.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
)
from trw_mcp.state.persistence import FileStateWriter

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a module-scope cycle
    from trw_mcp.state.claude_md._write_guard import InstructionWriteVerdict

logger = structlog.get_logger(__name__)

AT_IMPORT_PREFIX = "@"


def _extract_marker_inner(
    block: str,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
) -> str:
    """Return the content BETWEEN the TRW markers (for the sidecar body).

    Takes the marker pair so a client with its own sentinel vocabulary does not
    fall through to "return the whole block", which would copy that client's
    markers into the sidecar and leave them duplicated on both surfaces.
    """
    marker_start, marker_end = markers
    lines = block.splitlines()
    start: int | None = None
    end: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == marker_start and start is None:
            start = i
        elif stripped == marker_end:
            end = i
            break
    if start is not None and end is not None and end > start:
        return "\n".join(lines[start + 1 : end]).strip()
    return block.strip()


def _sidecar_document(
    rendered_block: str,
    sidecar_relpath: str,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
) -> str:
    """Build the externalized sidecar document from the rendered TRW block."""
    inner = _extract_marker_inner(rendered_block, markers)
    header = (
        f"<!-- TRW AUTO-GENERATED — do not edit. "
        f"Imported into your instruction file via {AT_IMPORT_PREFIX}{sidecar_relpath} (PRD-CORE-203). -->"
    )
    return f"{header}\n\n{inner}\n"


def render_import_region(
    sidecar_relpath: str,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
) -> str:
    """Render the marker-wrapped one-line import region placed into the file.

    *markers* lets a client keep its OWN sentinel vocabulary. Copilot's surface
    is delimited by ``trw:copilot:start/end``, and the uninstall registry plus
    doctor key on those — emitting the generic pair there would orphan the block
    from both (PRD-CORE-240-FR03).
    """
    marker_start, marker_end = markers
    return f"{TRW_AUTO_COMMENT}\n{marker_start}\n{AT_IMPORT_PREFIX}{sidecar_relpath}\n{marker_end}\n"


def is_path_within(root: Path, candidate: Path) -> bool:
    """Return whether *candidate* resolves to a path inside *root* (no traversal).

    PRD-CORE-203 P0-1: ``instruction_external_filename`` is operator-overridable
    (env / config.yaml); a value like ``../../etc/x`` would otherwise let the
    sidecar write escape the project root. ``resolve()`` normalizes ``..`` so the
    containment check is robust even for not-yet-existing paths.
    """
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def externalize_block(
    target: Path,
    *,
    rendered_block: str,
    sidecar_path: Path,
    sidecar_relpath: str,
    max_lines: int,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
    force: bool = False,
    dry_run: bool = False,
) -> InstructionWriteVerdict:
    """Externalize the TRW block: sidecar FIRST, then the import region (FR05).

    Writes *rendered_block* to *sidecar_path* before placing the
    ``@<sidecar_relpath>`` import region into *target*'s marker region — so a
    written import line always has a resolvable target (NFR02: no dangling
    import). The import region is merged via ``merge_trw_section`` so any prior
    inline block is cleanly replaced (migration) and user content outside the
    markers is preserved. Raises on any failure so the caller falls back to
    inline; a half-written sidecar is removed before re-raising (P2-1).
    """
    if not dry_run:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        writer = FileStateWriter()
        writer.write_text(sidecar_path, _sidecar_document(rendered_block, sidecar_relpath, markers))

    # Lazy import breaks the _parser <-> _instruction_carrier cycle.
    from trw_mcp.state.claude_md._parser import merge_trw_section

    try:
        verdict = merge_trw_section(
            target,
            render_import_region(sidecar_relpath, markers),
            max_lines,
            markers,
            force=force,
            dry_run=dry_run,
        )
    except Exception:
        # The import line never landed — drop the now-orphaned sidecar so it does
        # not linger unreferenced, then re-raise for the inline fallback.
        _drop_orphan_sidecar(sidecar_path, dry_run=dry_run)
        raise
    if not verdict.written and verdict.diff is None:
        # A guard REFUSAL is not an externalization failure to retry inline —
        # inline is strictly larger, so it would be refused too. Drop the sidecar
        # and surface the refusal.
        _drop_orphan_sidecar(sidecar_path, dry_run=dry_run)
    return verdict


def _drop_orphan_sidecar(sidecar_path: Path, *, dry_run: bool) -> None:
    """Remove a sidecar whose import line never landed (nothing was written on a dry run)."""
    if dry_run:
        return
    with contextlib.suppress(OSError):
        sidecar_path.unlink()


__all__ = [
    "AT_IMPORT_PREFIX",
    "externalize_block",
    "is_path_within",
    "render_import_region",
]
