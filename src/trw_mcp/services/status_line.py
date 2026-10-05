"""One-line status render for the Claude Code statusLine (PRD-CORE-354 FR03).

Pure: ``render_status_line(snapshot, width)`` turns a v1 snapshot from
:mod:`trw_mcp.services.status_snapshot` into one line such as::

    TRW ▸ implement · ckpt 12m · build ✓ · review – · deliver – · ✉2

Truthfulness (FR02): a positive tick needs evidence scoped to the run (or, for
build, to this session). Anything else -- aggregate-only evidence, an unreadable
source, a scope the renderer does not recognise -- renders as ``?``. The gate
preview is not rendered: a preview on a one-liner reads as a verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FALLBACK_LINE = "TRW · status unavailable"
NO_RUN_LINE = "TRW · no run"
DEGRADED_LINE = "TRW ⚠ MCP not seen"

_SEP = " · "
_UNKNOWN = "?"
_NONE = "–"
_POSITIVE_SCOPES = frozenset({"run", "session"})

_BUILD_MARKS = {"passed": "✓", "failed": "✗", "none": _NONE}
_REVIEW_MARKS = {"pass": "✓", "warn": "!", "block": "✗", "none": _NONE}
_DELIVER_MARKS = {"called": "✓", "none": _NONE}


def _block(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _format_age(age_s: int) -> str:
    if age_s < 60:
        return f"{age_s}s"
    if age_s < 3600:
        return f"{age_s // 60}m"
    if age_s < 86400:
        return f"{age_s // 3600}h"
    return f"{age_s // 86400}d"


def _checkpoint_text(checkpoint: Mapping[str, Any]) -> str:
    state = checkpoint.get("state")
    age = checkpoint.get("age_s")
    if state == "none":
        return f"ckpt {_NONE}"
    if state in {"ok", "stale"} and isinstance(age, int) and not isinstance(age, bool) and age >= 0:
        return f"ckpt {_format_age(age)}{'!' if state == 'stale' else ''}"
    return f"ckpt {_UNKNOWN}"


def _evidence_mark(item: Mapping[str, Any], marks: Mapping[str, str]) -> str:
    state = item.get("state")
    if not isinstance(state, str) or state not in marks:
        return _UNKNOWN
    if state != "none" and item.get("scope") not in _POSITIVE_SCOPES:
        # trw:intentional FR02 -- an outcome not scoped to this run/session is never a tick.
        return _UNKNOWN
    return marks[state]


def _inbox_text(inbox: Mapping[str, Any]) -> str | None:
    state = inbox.get("state")
    pending = inbox.get("pending")
    if state == "unknown":
        return f"✉{_UNKNOWN}"
    if state == "ok" and isinstance(pending, int) and not isinstance(pending, bool) and pending > 0:
        return f"✉{pending}"
    return None


def _fit(head: str, segments: list[tuple[int, str]], width: int | None) -> str:
    """Join, dropping the lowest-priority segments first, then hard-truncating."""
    kept = list(segments)
    line = _SEP.join([head, *(text for _, text in kept)])
    if width is None or width <= 0:
        return line
    while len(line) > width and kept:
        drop = min(range(len(kept)), key=lambda i: kept[i][0])
        kept.pop(drop)
        line = _SEP.join([head, *(text for _, text in kept)])
    if len(line) > width:
        line = line[: max(width - 1, 0)] + "…" if width > 1 else line[:width]
    return line


def render_status_line(snapshot: Mapping[str, Any] | None, width: int | None = None) -> str:
    """Render *snapshot* as one line no wider than *width* (``None`` = unbounded)."""
    if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != 1:
        return FALLBACK_LINE
    if _block(snapshot, "degraded").get("state") == "yes":
        return _fit(DEGRADED_LINE, [], width)
    run = _block(snapshot, "run")
    run_state = run.get("state")
    if run_state == "none":
        return _fit(NO_RUN_LINE, [], width)
    inbox = _inbox_text(_block(snapshot, "inbox"))
    phase = run.get("phase") if run_state == "ok" else None
    head = f"TRW ▸ {phase}" if isinstance(phase, str) and phase else f"TRW ▸ {_UNKNOWN}"
    evidence = _block(snapshot, "evidence")
    # (priority, text): higher priority survives width pressure longer.
    segments: list[tuple[int, str]] = [
        (5, _checkpoint_text(_block(snapshot, "checkpoint"))),
        (4, f"build {_evidence_mark(_block(evidence, 'build'), _BUILD_MARKS)}"),
        (3, f"review {_evidence_mark(_block(evidence, 'review'), _REVIEW_MARKS)}"),
        (2, f"deliver {_evidence_mark(_block(evidence, 'deliver'), _DELIVER_MARKS)}"),
    ]
    if inbox:
        segments.append((9, inbox))
    return _fit(head, segments, width)


__all__ = ["DEGRADED_LINE", "FALLBACK_LINE", "NO_RUN_LINE", "render_status_line"]
