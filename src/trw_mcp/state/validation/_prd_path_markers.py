"""Shared trailing planned-path marker detection for PRD validators."""

from __future__ import annotations

_GREENFIELD_MARKERS: tuple[str, ...] = ("(new)", "(planned)", "(future)")
_GREENFIELD_WINDOW_CHARS = 16


def has_trailing_planned_marker(content: str, token_end: int) -> bool:
    """Accept an adjacent marker after spaces/tabs within the trailing window.

    Do not borrow a marker from another token, line, or Markdown table cell.
    The window is bounded before stripping to preserve its existing cutoff.
    """
    window = content[token_end : token_end + _GREENFIELD_WINDOW_CHARS].casefold()
    return window.lstrip(" \t").startswith(_GREENFIELD_MARKERS)
