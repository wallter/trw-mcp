"""Idempotent marker-replace for channel distill segments.

Pure-function implementation extracted from bootstrap/_file_ops.py
(per PRD-DIST-2400 OQ-03) — no bootstrap dependency.

Satisfies FR12: idempotency invariant f(f(c,s),s)==f(c,s) and
bounded-diff property (content outside markers byte-identical).

PRD-DIST-2400 Phase C.
"""

from __future__ import annotations

from trw_mcp.channels._manifest_models import MarkersConfig

__all__ = [
    "extract_segment_interior",
    "replace_distill_segment",
]


def extract_segment_interior(content: str, markers: MarkersConfig) -> str | None:
    """Return text strictly between start and end markers, or None if absent.

    Used by detect_human_edit SHA256_SEGMENT mode and quota enforcement.

    Args:
        content: Full file content to search.
        markers: MarkersConfig with start and end marker strings.

    Returns:
        The interior string (may be empty) or None when either marker is
        missing or end appears before start.
    """
    start = markers.start
    end = markers.end
    if not start or not end:
        return None

    start_idx = content.find(start)
    if start_idx == -1:
        return None

    search_from = start_idx + len(start)
    end_idx = content.find(end, search_from)
    if end_idx == -1:
        return None

    return content[search_from:end_idx]


def replace_distill_segment(
    content: str,
    new_interior: str,
    *,
    markers: MarkersConfig,
) -> str:
    """Replace (or append) the distill segment bounded by *markers*.

    Behavior (mirrors bootstrap/_file_ops.py:smart_merge_marker_section but
    as a pure function operating on the interior rather than a full section):

    - Both markers found in correct order → replace content between them with
      *new_interior*.  The markers themselves are preserved byte-identical.
      Only the FIRST occurrence is replaced (count=1) to prevent duplicate
      marker pairs from accumulating across repeated renders.
    - Markers absent or out of order → append
      ``markers.start + "\\n" + new_interior + "\\n" + markers.end``
      at EOF with a one-blank-line separator (or no separator on empty input).

    Idempotency invariant (FR12):
        replace_distill_segment(replace_distill_segment(c, s, markers=m),
                                s, markers=m)
        == replace_distill_segment(c, s, markers=m)

    Bounded-diff property:
        Content before markers.start and after markers.end is byte-identical
        to the original in the returned string.

    Args:
        content: Current file contents (may be empty).
        new_interior: Replacement text to place between the markers.
        markers: MarkersConfig with start and end marker strings.

    Returns:
        Merged document string.
    """
    start = markers.start
    end = markers.end

    # --- locate first start marker ---
    start_idx = content.find(start)
    if start_idx != -1:
        end_idx = content.find(end, start_idx + len(start))
        if end_idx != -1:
            # Both markers present in correct order — replace interior only.
            before = content[: start_idx + len(start)]
            after = content[end_idx:]
            return before + "\n" + new_interior + "\n" + after

    # --- fallback: append at EOF ---
    section = start + "\n" + new_interior + "\n" + end
    if not content.strip():
        # Empty or whitespace-only content — return section with trailing newline
        return section + "\n"

    return content.rstrip() + "\n\n" + section + "\n"
