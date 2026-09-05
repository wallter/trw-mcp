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

# Externalization lives in the ``_carrier_externalize`` sibling (350-line gate).
# Re-exported here so import paths are unchanged AND so ``apply_carrier``
# resolves ``externalize_block`` as a module global — the seam existing tests
# monkeypatch.
from trw_mcp.state.claude_md._carrier_externalize import (
    AT_IMPORT_PREFIX as AT_IMPORT_PREFIX,
)
from trw_mcp.state.claude_md._carrier_externalize import (
    externalize_block as externalize_block,
)
from trw_mcp.state.claude_md._carrier_externalize import (
    is_path_within as is_path_within,
)
from trw_mcp.state.claude_md._carrier_externalize import (
    render_import_region as render_import_region,
)
from trw_mcp.state.claude_md._parser import (
    TRW_MARKER_END,
    TRW_MARKER_START,
)
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger(__name__)

# The include-incapable registry lives in ``_instruction_clients`` with the
# other client-tier data; re-exported here so existing importers are unchanged.
from trw_mcp.state.claude_md._instruction_clients import (  # noqa: E402
    INCLUDE_INCAPABLE_CLIENTS as INCLUDE_INCAPABLE_CLIENTS,
)

# Externalize knob values (mirror config Literal; centralized so the write path
# carries no magic strings).
EXTERNALIZE_OFF = "off"

#: Import syntaxes that can resolve an externalized sidecar. Both are in-file
#: ``@path`` directives resolved eagerly at session start; they differ only in
#: what paths are legal. ``at_path_repo_relative`` (Copilot) rejects absolute and
#: ``~``-rooted references, which is satisfied here because the sidecar path is
#: always repo-relative (``.trw/INSTRUCTIONS.md``) and containment is enforced by
#: :func:`is_path_within` before any write.
IMPORT_CAPABLE_SYNTAXES: frozenset[str] = frozenset({"at_path", "at_path_repo_relative"})


class CarrierMode(str, Enum):
    """How the TRW block is delivered into a target instruction file."""

    INLINE = "inline"
    IMPORT = "import"
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
    external_path: str | None = None
    pointer_targets: tuple[str, ...] = ()
    healed: bool = False
    refusal: InstructionWriteRefusalDict | None = None
    diff: InstructionDiffDict | None = None


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
    if externalize != EXTERNALIZE_OFF and import_syntax in IMPORT_CAPABLE_SYNTAXES and scope == "root":
        return CarrierMode.IMPORT
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
    markers: tuple[str, str] = (TRW_MARKER_START, TRW_MARKER_END),
    force: bool = False,
    dry_run: bool = False,
) -> CarrierOutcome:
    """Resolve and apply the carrier mode for *target* (FR04/FR05/FR06).

    Orchestrates the three carrier modes. POINTER targets are healed and left
    un-clobbered; import-capable targets are externalized to the sidecar (with
    an inline fallback on any failure — NFR02); everything else is inlined via
    ``merge_trw_section``.
    """
    classification = (
        classify_instruction_file(target, markers)
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
                verdict = externalize_block(
                    target,
                    rendered_block=rendered_block,
                    sidecar_path=sidecar_path,
                    sidecar_relpath=external_filename,
                    max_lines=max_lines,
                    markers=markers,
                    force=force,
                    dry_run=dry_run,
                )
                if verdict.written or verdict.diff is not None:
                    logger.info("instruction_externalized", target=str(target), sidecar=external_filename)
                return CarrierOutcome(
                    mode=mode,
                    total_lines=verdict.total_lines,
                    external_path=external_filename,
                    refusal=verdict.refusal,
                    diff=verdict.diff,
                )
            except Exception:  # justified: fail-open — externalization must degrade to inline, never dangle
                logger.warning(
                    "instruction_externalize_failed_fallback_inline",
                    target=str(target),
                    exc_info=True,
                )

    # INLINE — also the IMPORT fallback path.
    from trw_mcp.state.claude_md._parser import merge_trw_section

    verdict = merge_trw_section(target, rendered_block, max_lines, markers, force=force, dry_run=dry_run)
    return CarrierOutcome(
        mode=CarrierMode.INLINE,
        total_lines=verdict.total_lines,
        refusal=verdict.refusal,
        diff=verdict.diff,
    )
