"""Measurement, refusal construction, and floor evaluation for the write guard.

Belongs to the ``_write_guard.py`` seam (PRD-FIX-123). Split out so the guard
itself reads as one decision procedure and this module holds the arithmetic it
decides on. Everything here is pure except the scar warning, which logs.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts._ceremony import (
    InstructionDiffDict,
    InstructionRefusalReason,
    InstructionWriteRefusalDict,
)
from trw_mcp.state.claude_md._parser import (
    LEGACY_TRW_MARKER_END,
    LEGACY_TRW_MARKER_START,
    _block_cut_index,
    _marker_line_index,
)

#: The retired cursor-cli install dialect (PRD-CORE-243-FR06/FR08). No writer
#: emits it anymore; it is recognised here ONLY so a file mid-migration --
#: still carrying this dead block on the "current" side of a write-guard
#: comparison, already stripped of it on the "candidate" side by
#: ``_migrate_legacy_marker_block`` -- does not read as a non-generated
#: (user) content loss. See ``_parser.py`` for the strip itself.
_LEGACY_MARKERS: tuple[str, str] = (LEGACY_TRW_MARKER_START, LEGACY_TRW_MARKER_END)

logger = structlog.get_logger(__name__)

# The scar line a pre-PRD-FIX-123 release appended when it truncated a user's
# content. Assembled from fragments ON PURPOSE: FR01 asserts the whole literal
# is absent from ``trw-mcp/src/`` — no writer may emit it ever again — while
# detection still has to recognise damage a prior release already did.
_SCAR_TAIL = "truncated to line limit -->"
_SCAR_LINES: tuple[str, ...] = (f"<!-- trw: user content {_SCAR_TAIL}", f"<!-- trw: {_SCAR_TAIL}")


def non_generated_bytes(content: str, markers: tuple[str, str]) -> int:
    """Return the substantive byte count of everything OUTSIDE the TRW block.

    This is the quantity FR02's primary floor is measured on. Whitespace-only
    lines are dropped and each retained line is right-stripped before counting,
    because the merge legitimately normalises its own separators (it rstrips the
    region above the block and lstrips newlines below it). Counting raw bytes
    would let that normalisation register as user-content loss and refuse a
    correct write.

    Dialect-agnostic by construction: the caller passes its own marker pair, so
    ``<!-- trw:start -->`` and any other live dialect are handled without this
    module knowing the literal. The retired legacy dialect (``_LEGACY_MARKERS``)
    is ALWAYS additionally excluded, regardless of what the caller passed, so a
    migration write's dead-block removal never registers as user-content loss.
    """
    kept = _non_generated_lines(content, markers)
    return len("\n".join(line.rstrip() for line in kept if line.strip()).encode("utf-8"))


def _strip_marker_span(lines: list[str], markers: tuple[str, str]) -> list[str]:
    """Return *lines* with the first well-formed *markers* span removed."""
    start = _marker_line_index(lines, markers[0])
    end = _marker_line_index(lines, markers[1], after=start) if start is not None else None
    if start is None or end is None:
        return lines
    # ``_block_cut_index`` folds the auto-generated comment above the start
    # marker into the block, so a candidate that adds or drops that comment does
    # not read as a user-content change.
    return lines[: _block_cut_index(lines, start)] + lines[end + 1 :]


def _non_generated_lines(content: str, markers: tuple[str, str]) -> list[str]:
    """Return the lines of *content* that lie OUTSIDE every known generated block."""
    lines = _strip_marker_span(content.splitlines(), markers)
    if markers != _LEGACY_MARKERS:
        lines = _strip_marker_span(lines, _LEGACY_MARKERS)
    return lines


def _single_span_bytes(lines: list[str], markers: tuple[str, str]) -> int:
    """Return the RAW byte count of one *markers* span, or 0 when absent."""
    start = _marker_line_index(lines, markers[0])
    end = _marker_line_index(lines, markers[1], after=start) if start is not None else None
    if start is None or end is None:
        return 0
    span = lines[_block_cut_index(lines, start) : end + 1]
    return len("\n".join(span).encode("utf-8"))


def _generated_span_bytes(content: str, markers: tuple[str, str]) -> int:
    """Return the RAW byte count of every known generated block.

    Sums the caller's own *markers* span AND, unconditionally, the retired
    legacy dialect's span -- the same union :func:`_non_generated_lines`
    excludes, so the two measurements partition the file and their deltas are
    directly comparable. Without the legacy half, a migration write's dead
    legacy block disappearing would count as "total size shrank and TRW's own
    block does not explain it" and the shrink floor would refuse the write.
    """
    lines = content.splitlines()
    total = _single_span_bytes(lines, markers)
    if markers != _LEGACY_MARKERS:
        total += _single_span_bytes(lines, _LEGACY_MARKERS)
    return total


def _shrink_is_trw_accounted(
    current: str,
    candidate: str,
    markers: tuple[str, str],
    tolerance_bytes: int,
) -> bool:
    """Return whether a total-size drop is explained by TRW's OWN region shrinking.

    Quantitative, not structural. An earlier version exempted any candidate that
    merely CARRIED a well-formed marker pair — which is nearly every candidate,
    so the secondary floor was effectively switched off and the primary
    non-generated measurement had no backstop at all. The test is now: does the
    marker span's own byte delta account for the drop?

    That keeps both shipped collapses exempt, because in both the block IS what
    shrank — PRD-CORE-203 externalization replaces a full inline block with a
    one-line ``@sidecar`` import, and ``_migrate_trw_content_from_agents_md``
    removes the block entirely (FR02 names the latter explicitly as a write that
    must not be refused). And it leaves the floor live for the case the primary
    measurement is blind to: raw non-block bytes disappearing that whitespace
    normalisation makes invisible.

    *tolerance_bytes* absorbs the separator churn the merge itself produces.
    """
    total_drop = len(current.encode("utf-8")) - len(candidate.encode("utf-8"))
    if total_drop <= 0:
        return True
    block_delta = _generated_span_bytes(current, markers) - _generated_span_bytes(candidate, markers)
    return block_delta + tolerance_bytes >= total_drop


def build_refusal(
    target: Path,
    reason: InstructionRefusalReason,
    detail: str,
    *,
    lines: int,
    limit: int,
    counts: tuple[int, int, int, int],
) -> InstructionWriteRefusalDict:
    """Build the structured refusal payload."""
    current_non_generated, candidate_non_generated, current_total, candidate_total = counts
    return {
        "error_code": "instruction_surface_oversized" if reason == "oversized" else "instruction_write_refused",
        "file": str(target),
        "reason": reason,
        "lines": lines,
        "limit": limit,
        "current_non_generated_bytes": current_non_generated,
        "candidate_non_generated_bytes": candidate_non_generated,
        "current_total_bytes": current_total,
        "candidate_total_bytes": candidate_total,
        "detail": detail,
    }


def build_diff(target: Path, current: str, candidate: str, cap: int) -> InstructionDiffDict:
    """Render the bounded unified diff returned by a dry run (FR03/NFR03)."""
    rendered = list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            n=3,
        )
    )
    truncated = len(rendered) > cap
    if truncated:
        rendered = rendered[:cap]
    return {
        "file": str(target),
        "diff": "".join(rendered),
        "diff_truncated": truncated,
        "diff_line_cap": cap,
    }


def warn_prior_truncation(target: Path, current: str) -> None:
    """Report a truncation scar left by a pre-fix release, once per write attempt (FR01)."""
    for line in current.splitlines():
        if line.strip() in _SCAR_LINES:
            logger.warning("instruction_prior_truncation_detected", path=str(target))
            return


def evaluate_floors(
    target: Path,
    *,
    counts: tuple[int, int, int, int],
    candidate_lines: int,
    max_lines: int | None,
    current: str | None,
    candidate: str,
    markers: tuple[str, str],
    force: bool,
    enforce_shrink_floor: bool,
    shrink_fraction: float,
    block_delta_tolerance_bytes: int,
) -> InstructionWriteRefusalDict | None:
    """Apply the FR01 overflow refusal and the two FR02 floors, in that order."""
    current_ng, candidate_ng, current_total, candidate_total = counts
    has_current = current is not None

    if max_lines is not None and candidate_lines > max_lines:
        # FR01: overflow is a condition the writer is not authorised to resolve
        # unilaterally. There is a third option beyond "cut TRW" and "cut the
        # user": write nothing and say so.
        return build_refusal(
            target,
            "oversized",
            f"merged content is {candidate_lines} lines, exceeding the {max_lines}-line limit; "
            "raise max_auto_lines or shorten the file — TRW will not truncate your content",
            lines=candidate_lines,
            limit=max_lines,
            counts=counts,
        )

    if not has_current or force or not enforce_shrink_floor:
        return None

    if candidate_ng < current_ng:
        # Checked BEFORE the total floor so an incident that grows the file while
        # destroying user content is always reported on the quantity that saw it.
        return build_refusal(
            target,
            "non_generated_shrink",
            f"write would reduce non-generated content from {current_ng} to {candidate_ng} bytes",
            lines=candidate_lines,
            limit=max_lines or 0,
            counts=counts,
        )

    # Secondary floor, on the TOTAL. It backstops the case the primary floor is
    # blind to: the file collapses, the user's region reads as intact under the
    # whitespace-normalised measurement, and TRW's own block did not shrink
    # enough to account for the missing bytes.
    floor = (1.0 - shrink_fraction) * current_total
    if candidate_total < floor and not _shrink_is_trw_accounted(
        current or "", candidate, markers, block_delta_tolerance_bytes
    ):
        return build_refusal(
            target,
            "total_shrink",
            f"write would reduce total size from {current_total} to {candidate_total} bytes, "
            f"below the {floor:.0f}-byte floor",
            lines=candidate_lines,
            limit=max_lines or 0,
            counts=counts,
        )
    return None


__all__ = [
    "build_diff",
    "build_refusal",
    "evaluate_floors",
    "non_generated_bytes",
    "warn_prior_truncation",
]
