"""Feedback nudge engine (PRD-INFRA-132 FR07).

Tracks per-session signals that suggest the user may have hit a TRW bug,
and surfaces a single ``/trw-feedback`` reminder once any signal crosses
its configured threshold.

Design notes
------------

This module sits ALONGSIDE ``_nudge_state.py`` rather than on top of it.
The ceremony nudge engine is a separate, larger surface concerned with
phase progress; the feedback nudge is a narrow counter+throttle engine
with a frozen one-line emission text. Keeping them separate preserves
the 350 effective-LOC module-size budget for each.

Opt-in gate (NFR04)
-------------------

``maybe_emit_feedback_nudge`` ALWAYS returns ``None`` when
``config.feedback.proactive`` is ``False``. Counters may still be
recorded, but no user-visible text is produced.

Throttle
--------

Once a session has been nudged, ``nudge_emitted`` flips to ``True`` for
that session and subsequent calls return ``None`` for the lifetime of
the session, even if counters continue to climb.

State file shape (JSON)
-----------------------

``<trw_dir>/runtime/feedback_nudge_state.json``::

    {
      "<session_id>": {
        "build_check_fail_count": int,
        "unhandled_exception_count": int,
        "bug_learning_count": int,
        "nudge_emitted": bool
      },
      ...
    }

Writes are atomic: write to ``.tmp`` then ``os.replace`` onto the final
path so a crash mid-write leaves the prior good state intact.

Wiring gap (out of scope for FR07 first land)
---------------------------------------------

The engine never fires in prod until call sites are wired in. Hook
points for the audit follow-up:

* ``record_build_check_outcome(session_id, passed, trw_dir)`` ->
  call from ``trw_mcp.tools.build`` at the tail of ``trw_build_check``
  using the same ``trw_dir`` already resolved there. Counter resets
  to zero on ``passed=True`` (consecutive failures only).
* ``record_unhandled_exception(session_id, trw_dir)`` ->
  call from ``trw_mcp.security.anomaly_stats`` (or wherever unhandled
  tool exceptions are first detected) once per exception per session.
* ``record_bug_learning(session_id, tags, trw_dir)`` ->
  call from ``trw_mcp.tools.learning`` after a successful ``trw_learn``
  with the entry's ``tags`` list. The helper itself filters to entries
  tagged BOTH ``bug`` AND ``trw-internal``.

Until those call sites are wired, the engine is a no-op in prod.
"""

from __future__ import annotations

import structlog
from typing_extensions import TypedDict

logger = structlog.get_logger(__name__)


class _SessionCounters(TypedDict):
    """Per-session counter shape persisted in the JSON state file."""

    build_check_fail_count: int
    unhandled_exception_count: int
    bug_learning_count: int
    nudge_emitted: bool


__all__ = []
