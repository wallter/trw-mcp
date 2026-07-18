"""Instruction-file carrier resolution: pointer detection, externalization, healing.

PRD-CORE-203. A *carrier* is how the TRW auto-generated block reaches a client
instruction file (CLAUDE.md / AGENTS.md):

- ``INLINE``       — the full block is written between the TRW markers
  (legacy default; byte-identical to pre-203 behaviour).
- ``IMPORT``       — the block is externalized to a ``.trw/`` sidecar and a
  single ``@<sidecar>`` import directive sits in the marker region. Only for
  clients whose profile declares ``instruction_import_syntax == "at_path"``
  (Claude Code's recursive ``@path`` import). Keeps tracked instruction files
  short and moves the artifact back into ``.trw/``.
- ``POINTER_SKIP`` — the file is a thin *single-source pointer* (its only
  substantive lines are import directives, e.g. a CLAUDE.md that is just
  ``@AGENTS.md``). TRW leaves it un-clobbered and heals any stale block that an
  older append-when-no-markers sync left behind.

This module is the **single shared guard** consumed by BOTH appender paths —
``_parser.merge_trw_section`` and bootstrap ``_update_claude_md_trw_section`` —
so they agree on pointer handling (FR04, DRY). It imports only marker *constants*
from ``_parser`` at module scope; the heavier ``merge_trw_section`` is imported
lazily inside functions to avoid an import cycle.

Belongs to the ``state/claude_md`` package; the public symbols are re-exported
through ``state/claude_md/__init__.py``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import structlog

# Classification helpers live in the ``_carrier_classify`` sibling (350-line
# gate); re-exported here so the facade import path is stable and so the callers
# below (``apply_carrier``/``pointer_skip_guard``/``resolve_carrier_mode``)
# resolve them as module globals — preserving test monkeypatch seams.
from trw_mcp.state.claude_md._carrier_classify import (
    InstructionFileClass as InstructionFileClass,
)
from trw_mcp.state.claude_md._carrier_classify import (
    InstructionFileClassification as InstructionFileClassification,
)
from trw_mcp.state.claude_md._carrier_classify import (
    classify_instruction_file as classify_instruction_file,
)
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
)
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger(__name__)

AT_IMPORT_PREFIX = "@"

# Externalize knob values (mirror config Literal; centralized so the write path
# carries no magic strings).
EXTERNALIZE_OFF = "off"


class CarrierMode(str, Enum):
    """How the TRW block is delivered into a target instruction file."""

    INLINE = "inline"
    IMPORT = "import"
    POINTER_SKIP = "pointer_skip"


@dataclass(frozen=True)
class CarrierOutcome:
    """Result of :func:`apply_carrier` — what was written and how."""

    mode: CarrierMode
    total_lines: int = 0
    external_path: str | None = None
    pointer_targets: tuple[str, ...] = ()
    healed: bool = False


def resolve_carrier_mode(
    classification: InstructionFileClassification,
    *,
    import_syntax: str,
    externalize: str,
    scope: str,
) -> CarrierMode:
    """Pure decision: which carrier mode applies for a target (FR04/FR05)."""
    if classification.kind is InstructionFileClass.POINTER:
        return CarrierMode.POINTER_SKIP
    if externalize != EXTERNALIZE_OFF and import_syntax == "at_path" and scope == "root":
        return CarrierMode.IMPORT
    return CarrierMode.INLINE


def _extract_marker_inner(block: str) -> str:
    """Return the content BETWEEN the TRW markers (for the sidecar body)."""
    lines = block.splitlines()
    start: int | None = None
    end: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == TRW_MARKER_START and start is None:
            start = i
        elif stripped == TRW_MARKER_END:
            end = i
            break
    if start is not None and end is not None and end > start:
        return "\n".join(lines[start + 1 : end]).strip()
    return block.strip()


def _sidecar_document(rendered_block: str, sidecar_relpath: str) -> str:
    """Build the externalized sidecar document from the rendered TRW block."""
    inner = _extract_marker_inner(rendered_block)
    header = (
        f"<!-- TRW AUTO-GENERATED — do not edit. "
        f"Imported into your instruction file via {AT_IMPORT_PREFIX}{sidecar_relpath} (PRD-CORE-203). -->"
    )
    return f"{header}\n\n{inner}\n"


def render_import_region(sidecar_relpath: str) -> str:
    """Render the marker-wrapped one-line import region placed into the file."""
    return f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n{AT_IMPORT_PREFIX}{sidecar_relpath}\n{TRW_MARKER_END}\n"


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
) -> int:
    """Externalize the TRW block: sidecar FIRST, then the import region (FR05).

    Writes *rendered_block* to *sidecar_path* before placing the
    ``@<sidecar_relpath>`` import region into *target*'s marker region — so a
    written import line always has a resolvable target (NFR02: no dangling
    import). The import region is merged via ``merge_trw_section`` so any prior
    inline block is cleanly replaced (migration) and user content outside the
    markers is preserved. Raises on any failure so the caller falls back to
    inline; a half-written sidecar is removed before re-raising (P2-1).
    """
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    writer = FileStateWriter()
    writer.write_text(sidecar_path, _sidecar_document(rendered_block, sidecar_relpath))

    # Lazy import breaks the _parser <-> _instruction_carrier cycle.
    from trw_mcp.state.claude_md._parser import merge_trw_section

    try:
        return merge_trw_section(target, render_import_region(sidecar_relpath), max_lines)
    except Exception:
        # The import line never landed — drop the now-orphaned sidecar so it does
        # not linger unreferenced, then re-raise for the inline fallback.
        with contextlib.suppress(OSError):
            sidecar_path.unlink()
        raise


def heal_pointer(target: Path) -> bool:
    """Strip a stale appended TRW block from a pointer file (FR06). Idempotent.

    Reuses ``_strip_trw_section`` (which removes the marker region plus its
    leading auto-comment) and normalizes trailing whitespace so a re-run is a
    byte-identical no-op. Only ever removes a TRW-marked region; never user
    content (NFR02).
    """
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    from trw_mcp.state.claude_md._agents_md import _strip_trw_section

    stripped, remaining = _strip_trw_section(content)
    if not stripped:
        return False
    healed = remaining.rstrip() + "\n"
    if healed == content:
        return False
    try:
        FileStateWriter().write_text(target, healed)
    except OSError:
        logger.warning("instruction_pointer_heal_write_failed", target=str(target), exc_info=True)
        return False
    return True


def pointer_skip_guard(target: Path) -> InstructionFileClassification | None:
    """Shared FR04 guard for both appenders.

    Classify *target*; if it is a thin single-source pointer, heal any stale
    appended block and return the classification — the caller MUST then skip the
    append/replace. Otherwise return ``None`` (caller proceeds normally).
    """
    classification = classify_instruction_file(target)
    if classification.kind is InstructionFileClass.POINTER:
        heal_pointer(target)
        return classification
    return None


def apply_carrier(
    target: Path,
    rendered_block: str,
    max_lines: int,
    *,
    import_syntax: str,
    externalize: str,
    scope: str,
    external_filename: str,
    project_root: Path,
) -> CarrierOutcome:
    """Resolve and apply the carrier mode for *target* (FR04/FR05/FR06).

    Orchestrates the three carrier modes. POINTER targets are healed and left
    un-clobbered; import-capable targets are externalized to the sidecar (with
    an inline fallback on any failure — NFR02); everything else is inlined via
    ``merge_trw_section``.
    """
    classification = (
        classify_instruction_file(target)
        if target.exists()
        else InstructionFileClassification(InstructionFileClass.EMPTY)
    )
    mode = resolve_carrier_mode(
        classification,
        import_syntax=import_syntax,
        externalize=externalize,
        scope=scope,
    )

    if mode is CarrierMode.POINTER_SKIP:
        healed = heal_pointer(target)
        lines = len(target.read_text(encoding="utf-8").splitlines()) if target.exists() else 0
        logger.info(
            "instruction_pointer_skipped",
            target=str(target),
            import_targets=list(classification.import_targets),
            healed=healed,
        )
        return CarrierOutcome(
            mode=mode,
            total_lines=lines,
            pointer_targets=classification.import_targets,
            healed=healed,
        )

    if mode is CarrierMode.IMPORT:
        sidecar_path = project_root / external_filename
        # P0-1: refuse a sidecar path that escapes the project root; degrade to
        # inline rather than writing outside the repo.
        if not is_path_within(project_root, sidecar_path):
            logger.warning(
                "instruction_external_filename_escapes_root_fallback_inline",
                external_filename=external_filename,
                project_root=str(project_root),
            )
        else:
            try:
                lines = externalize_block(
                    target,
                    rendered_block=rendered_block,
                    sidecar_path=sidecar_path,
                    sidecar_relpath=external_filename,
                    max_lines=max_lines,
                )
                logger.info("instruction_externalized", target=str(target), sidecar=external_filename)
                return CarrierOutcome(mode=mode, total_lines=lines, external_path=external_filename)
            except Exception:  # justified: fail-open — externalization must degrade to inline, never dangle
                logger.warning(
                    "instruction_externalize_failed_fallback_inline",
                    target=str(target),
                    exc_info=True,
                )

    # INLINE — also the IMPORT fallback path.
    from trw_mcp.state.claude_md._parser import merge_trw_section

    lines = merge_trw_section(target, rendered_block, max_lines)
    return CarrierOutcome(mode=CarrierMode.INLINE, total_lines=lines)
