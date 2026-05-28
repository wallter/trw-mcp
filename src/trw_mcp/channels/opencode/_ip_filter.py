"""Render-time IP filter for opencode distill channels.

Strips proprietary paths (those starting with ``trw-distill/``) from any
list of file paths before they are committed to AGENTS.md or any other
user-visible artifact.

Audit fix P2-10: prevents internal trw-distill package paths from leaking
into the publicly-committed AGENTS.md distill segment.

Zero trw_distill imports.

PRD-DIST-2403 FR07.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "filter_proprietary_paths",
]

# Path prefix that identifies proprietary internal trw-distill modules.
_PROPRIETARY_PREFIX = "trw-distill/"


def filter_proprietary_paths(paths: list[str]) -> list[str]:
    """Remove paths starting with ``trw-distill/`` from *paths*.

    Records the count of filtered paths at DEBUG level.

    Args:
        paths: List of repo-relative file paths from a sidecar hotspot list.

    Returns:
        New list with proprietary paths removed.  Order is preserved.
    """
    filtered: list[str] = []
    excluded: list[str] = []

    for p in paths:
        if p.startswith(_PROPRIETARY_PREFIX):
            excluded.append(p)
        else:
            filtered.append(p)

    if excluded:
        log.debug(
            "opencode_ip_filter_applied",
            ip_filtered_paths=len(excluded),
            excluded_count=len(excluded),
            total_input=len(paths),
            outcome="filtered",
        )

    return filtered
