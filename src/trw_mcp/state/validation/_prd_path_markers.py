"""Shared trailing planned-path marker detection for PRD validators."""

from __future__ import annotations

_GREENFIELD_MARKERS: tuple[str, ...] = ("(new)", "(planned)", "(future)")
_GREENFIELD_WINDOW_CHARS = 16


def has_trailing_planned_marker(content: str, token_end: int) -> bool:
    """Return whether a supported marker occurs in the token's trailing window."""
    window = content[token_end : token_end + _GREENFIELD_WINDOW_CHARS].casefold()
    return any(marker in window for marker in _GREENFIELD_MARKERS)
