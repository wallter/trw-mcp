"""Metadata preparation helpers for ``execute_learn``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from trw_mcp.tools._learning_helpers import truncate_nudge_line


class _LearnLogger(Protocol):
    def warning(self, event: str, **kwargs: object) -> object: ...

    def debug(self, event: str, **kwargs: object) -> object: ...


def resolve_phase_origin(phase_origin: str, log: _LearnLogger) -> str:
    """Return explicit ``phase_origin`` or fail-open auto-detected phase."""
    if phase_origin:
        return phase_origin
    try:
        from trw_mcp.state._paths import detect_current_phase

        detected = detect_current_phase()
        if detected:
            return detected.upper()
        log.warning("phase_origin_no_active_run")
    except Exception:  # justified: fail-open
        log.warning("phase_origin_detection_failed", exc_info=True)
    return phase_origin


def prepare_nudge_line(nudge_line: str, summary: str) -> str:
    """Auto-generate a bounded nudge line from the summary when omitted."""
    return truncate_nudge_line(nudge_line or summary)


def prepare_tags(
    tags: list[str] | None,
    *,
    summary: str,
    is_solution_fn: Callable[[str], bool] | None,
    default_is_solution: Callable[[str], bool],
    log: _LearnLogger,
) -> list[str]:
    """Return tags with the solution-pattern auto-tag applied."""
    safe_tags = list(tags or [])
    is_solution = is_solution_fn if callable(is_solution_fn) else default_is_solution
    if is_solution(summary) and "pattern" not in safe_tags:
        safe_tags.append("pattern")
        log.debug("pattern_tag_auto_added", summary=summary[:60])
    return safe_tags


__all__ = ["prepare_nudge_line", "prepare_tags", "resolve_phase_origin"]
