"""Instruction-file carrier resolution: pointer detection and healing.

PRD-CORE-203, narrowed by PRD-QUAL-143-FR01. A *carrier* is how the TRW
auto-generated block reaches a client instruction file (CLAUDE.md / AGENTS.md):

- ``INLINE``       — the full block is written between the TRW markers.
- ``POINTER_SKIP`` — the file is a thin *single-source pointer* (its only
  substantive lines are import directives, e.g. a CLAUDE.md that is just
  ``@AGENTS.md``). TRW leaves it un-clobbered and heals any stale block that an
  older append-when-no-markers sync left behind.

The ``.trw`` sidecar mode is gone: it made a second writer for the same content,
and the sidecar went stale while the inline block stayed correct.

This module is the **single shared guard** consumed by BOTH appender paths —
``_parser.merge_trw_section`` and bootstrap ``_update_claude_md_trw_section`` —
so they agree on pointer handling (FR04, DRY). It imports only marker *constants*
from ``_parser`` at module scope; the heavier ``merge_trw_section`` is imported
lazily inside functions to avoid an import cycle.

Belongs to the ``state/claude_md`` package; the public symbols are re-exported
through ``state/claude_md/__init__.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts._ceremony import (
    InstructionDiffDict,
    InstructionWriteRefusalDict,
)

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
    TRW_MARKER_END,
    TRW_MARKER_START,
)
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger(__name__)


class CarrierMode(str, Enum):
    """How the TRW block is delivered into a target instruction file."""

    INLINE = "inline"
    POINTER_SKIP = "pointer_skip"


@dataclass(frozen=True)
class CarrierOutcome:
    """Result of :func:`apply_carrier` — what was written and how.

    ``refusal`` and ``diff`` carry the PRD-FIX-123 guard verdict outward: a
    refused write is a policy outcome the caller must report, not an exception
    and not a silent success.
    """

    mode: CarrierMode
    total_lines: int = 0
    pointer_targets: tuple[str, ...] = ()
    healed: bool = False
    refusal: InstructionWriteRefusalDict | None = None
    diff: InstructionDiffDict | None = None


def resolve_carrier_mode(classification: InstructionFileClassification) -> CarrierMode:
    """Pure decision: a pointer file is skipped, everything else is inlined."""
    if classification.kind is InstructionFileClass.POINTER:
        return CarrierMode.POINTER_SKIP
    return CarrierMode.INLINE


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


def pointer_skip_guard(target: Path, *, dry_run: bool = False) -> InstructionFileClassification | None:
    """Shared FR04 guard for both appenders.

    Classify *target*; if it is a thin single-source pointer, heal any stale
    appended block (unless *dry_run*) and return the classification — the caller
    MUST then skip the append/replace. Otherwise return ``None`` (caller proceeds
    normally).
    """
    classification = classify_instruction_file(target)
    if classification.kind is InstructionFileClass.POINTER:
        if not dry_run:
            heal_pointer(target)
        return classification
    return None


def apply_carrier(
    target: Path,
    rendered_block: str,
    max_lines: int | None,
    *,
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
    force: bool = False,
    dry_run: bool = False,
) -> CarrierOutcome:
    """Heal and skip a pointer *target*; otherwise merge the block inline."""
    classification = (
        classify_instruction_file(target, markers)
        if target.exists()
        else InstructionFileClassification(InstructionFileClass.EMPTY)
    )
    mode = resolve_carrier_mode(classification)

    if mode is CarrierMode.POINTER_SKIP:
        # A dry run writes nothing at all, healing included.
        healed = False if dry_run else heal_pointer(target)
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

    from trw_mcp.state.claude_md._parser import merge_trw_section

    verdict = merge_trw_section(target, rendered_block, max_lines, markers, force=force, dry_run=dry_run)
    return CarrierOutcome(
        mode=CarrierMode.INLINE,
        total_lines=verdict.total_lines,
        refusal=verdict.refusal,
        diff=verdict.diff,
    )
