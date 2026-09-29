"""Pre-spawn failure results for the dispatch runner.

Belongs to the ``_runner.py`` facade, which re-exports ``_early_result`` (split out to keep ``_runner.py`` under
the 350 effective-LOC gate when PRD-SEC-015 FR08 added the enforcement report).
"""

from __future__ import annotations

from trw_mcp.dispatch._enforcement_layers import NO_CHILD_ENFORCEMENT
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult


def _early_result(
    req: DispatchRequest,
    argv_redacted: list[str],
    *,
    exit_code: int,
    stderr: str,
    silence_reason: str = "nonzero_exit",
) -> DispatchResult:
    """Build a clean failure result for a pre-spawn / launch failure (no child).

    ``posture_enforced`` and ``trw_access_enforced`` are False on every one of
    these paths by construction: no child was launched, so nothing was bounded
    and nothing was connected. Reporting the spec's capability here would claim
    containment -- or a TRW connection -- for a process that never existed.

    ``read_only_enforced`` is False for the same reason: a wrapper prefix that
    was found but never ran enforced nothing (a missing agy binary once
    reported enforced containment here).

    ``enforcement_layers`` is ``()`` for the same reason again: no child means
    no layer was verified, so the tuple is empty with a note naming this a
    pre-spawn refusal rather than an unmeasured client/isolate row.
    """
    return DispatchResult(
        client=req.client,
        argv_redacted=argv_redacted,
        read_only_enforced=False,
        posture=req.posture,
        posture_note=req.posture_note,
        posture_enforced=False,
        trw_access_enforced=False,
        **NO_CHILD_ENFORCEMENT,
        exit_code=exit_code,
        timed_out=False,
        duration_s=0.0,
        text="",
        raw_stdout="",
        raw_stderr=stderr,
        structured=None,
        silence_reason=silence_reason,
    )
