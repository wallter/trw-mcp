"""One-line status label for the Claude Code statusLine (PRD-CORE-354 FR03).

Pure: ``render_status_line(snapshot, width)`` turns a v1 snapshot from
:mod:`trw_mcp.services.status_snapshot` into a short plain-text label::

    TRW ▸ implement · fix the parser · build ✗ · ✉2

Exceptions only: no times, no checkpoint age, no positive ticks. Build, review
and deliver evidence is shown only when it is scoped to the run (or, for build,
the session) and is a failure or block (FR02: aggregate-only evidence is never
trusted). The gate preview is not rendered.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

FALLBACK_LINE = "TRW · status unavailable"
NO_RUN_LINE = "TRW"
DEGRADED_LINE = "TRW ⚠ MCP not seen"
UNKNOWN_LINE = "TRW ?"

_SEP = " · "
_TASK_MAX = 24
_POSITIVE_SCOPES = frozenset({"run", "session"})


def _block(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _scoped_state(item: Mapping[str, Any]) -> object:
    # trw:intentional FR02 -- an outcome not scoped to this run/session is never shown.
    return item.get("state") if item.get("scope") in _POSITIVE_SCOPES else None


def _exceptions(snapshot: Mapping[str, Any]) -> list[str]:
    evidence = _block(snapshot, "evidence")
    found: list[str] = []
    if _scoped_state(_block(evidence, "build")) == "failed":
        found.append("build ✗")
    if _scoped_state(_block(evidence, "review")) == "block":
        found.append("review ✗")
    inbox = _block(snapshot, "inbox")
    pending = inbox.get("pending")
    if inbox.get("state") == "ok" and isinstance(pending, int) and not isinstance(pending, bool) and pending > 0:
        found.append(f"✉{pending}")
    return found


_CONTROL = re.compile(r"[\u0000-\u001f\u007f-\u009f]")


def _clean_cut(value: object, limit: int = _TASK_MAX) -> str:
    """Control chars (C0, DEL, C1) -> space, collapse whitespace, cut to *limit* code points."""
    if not isinstance(value, str):
        return ""
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clip(line: str, width: int | None) -> str:
    """Clip the tail to *width* columns ending in an ellipsis, only when something is cut."""
    if width is None or len(line) <= width:
        return line
    return line[: max(0, width - 1)] + "…"


def _compose(
    delivered: bool, phase: str, task: str, exceptions: list[str], *, keep_task: bool, keep_phase: bool
) -> str:
    if delivered:
        head = "TRW ✓" + (f" {task}" if keep_task and task else "")
    else:
        head = f"TRW ▸ {phase}" if keep_phase else "TRW"
        if keep_task and task:
            head += f"{_SEP}{task}"
    return _SEP.join([head, *exceptions])


def render_status_line(snapshot: Mapping[str, Any] | None, width: int | None = None) -> str:
    """Render *snapshot* as one line no wider than *width* (``None`` = unbounded).

    Parity: output matches the shared golden table in the trw-ui mod
    (``label-golden.ts``) exactly. The one deliberate difference is invalid input
    (not a v1 snapshot): this renderer returns :data:`FALLBACK_LINE`, while the mod
    shows ``TRW ?`` there (spec carve-out).
    """
    if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != 1:
        return FALLBACK_LINE
    if _block(snapshot, "degraded").get("state") == "yes":
        return _clip(DEGRADED_LINE, width)
    run = _block(snapshot, "run")
    run_state = run.get("state")
    if run_state == "none":
        return _clip(NO_RUN_LINE, width)
    if run_state != "ok":
        return _clip(UNKNOWN_LINE, width)
    delivered = _scoped_state(_block(_block(snapshot, "evidence"), "deliver")) == "called"
    phase = _clean_cut(run.get("phase")) or "?"
    task = _clean_cut(run.get("task"))
    exceptions = _exceptions(snapshot)
    tries = [
        _compose(delivered, phase, task, exceptions, keep_task=True, keep_phase=True),
        _compose(delivered, phase, task, exceptions, keep_task=False, keep_phase=True),
        _compose(delivered, phase, task, exceptions, keep_task=False, keep_phase=False),
    ]
    if width is None:
        return tries[0]
    for line in tries:
        if len(line) <= width:
            return line
    return _clip(tries[2], width)


__all__ = ["DEGRADED_LINE", "FALLBACK_LINE", "NO_RUN_LINE", "UNKNOWN_LINE", "render_status_line"]
