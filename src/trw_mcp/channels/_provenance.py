"""Canonical provenance comment/frontmatter renderer.

All channel renderers call render_provenance_comment() and prepend the
result to their channel content (PRD-DIST-2400 FR19).

Single-line variants from earlier plans are deprecated; this multiline
format is the only canonical form.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = [
    "now_utc_iso8601",
]


def now_utc_iso8601() -> str:
    """Return UTC timestamp with millisecond precision and Z suffix.

    Example: ``2026-05-28T12:34:56.789Z``
    """
    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
