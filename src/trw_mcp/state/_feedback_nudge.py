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

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig


FEEDBACK_NUDGE_TEXT = (
    "If this looks like a TRW bug, consider /trw-feedback to file a report."
)
"""Frozen nudge text. Single source of truth -- tests assert against this."""


class _SessionCounters(TypedDict):
    """Per-session counter shape persisted in the JSON state file."""

    build_check_fail_count: int
    unhandled_exception_count: int
    bug_learning_count: int
    nudge_emitted: bool


_State = dict[str, _SessionCounters]


def _state_path(trw_dir: Path) -> Path:
    """Return the absolute path of the feedback nudge state file."""
    return trw_dir / "runtime" / "feedback_nudge_state.json"


def _empty_counters() -> _SessionCounters:
    """Return a fresh zero-initialized counter record."""
    return _SessionCounters(
        build_check_fail_count=0,
        unhandled_exception_count=0,
        bug_learning_count=0,
        nudge_emitted=False,
    )


def _coerce_session(raw: object) -> _SessionCounters:
    """Validate and coerce one session record from disk into the TypedDict.

    Unknown keys are dropped; missing keys fall back to zero / False.
    """
    if not isinstance(raw, dict):
        return _empty_counters()
    counters = _empty_counters()
    bc = raw.get("build_check_fail_count")
    if isinstance(bc, int) and bc >= 0:
        counters["build_check_fail_count"] = bc
    ue = raw.get("unhandled_exception_count")
    if isinstance(ue, int) and ue >= 0:
        counters["unhandled_exception_count"] = ue
    bl = raw.get("bug_learning_count")
    if isinstance(bl, int) and bl >= 0:
        counters["bug_learning_count"] = bl
    nudge_emitted = raw.get("nudge_emitted")
    if isinstance(nudge_emitted, bool):
        counters["nudge_emitted"] = nudge_emitted
    return counters


def _read_state(trw_dir: Path) -> _State:
    """Read the on-disk state, returning ``{}`` if absent or unreadable."""
    path = _state_path(trw_dir)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: _State = {}
    for key, value in data.items():
        if isinstance(key, str):
            out[key] = _coerce_session(value)
    return out


def _write_state(trw_dir: Path, state: _State) -> None:
    """Atomically persist ``state`` to disk.

    Writes to a sibling ``.tmp`` file first and then ``os.replace`` onto
    the canonical path so concurrent readers always see either the prior
    full state or the new full state, never a partial.
    """
    path = _state_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable: dict[str, dict[str, int | bool]] = {
        sid: {
            "build_check_fail_count": counters["build_check_fail_count"],
            "unhandled_exception_count": counters["unhandled_exception_count"],
            "bug_learning_count": counters["bug_learning_count"],
            "nudge_emitted": counters["nudge_emitted"],
        }
        for sid, counters in state.items()
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=".feedback_nudge_state.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(serializable, fh, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    except OSError:
        # Best-effort cleanup of the temp file on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _get_or_create(state: _State, session_id: str) -> _SessionCounters:
    """Return the counter record for ``session_id``, creating if absent."""
    if session_id not in state:
        state[session_id] = _empty_counters()
    return state[session_id]


def record_build_check_outcome(
    session_id: str, passed: bool, trw_dir: Path
) -> None:
    """Record a ``trw_build_check`` outcome for the session.

    Consecutive failures increment ``build_check_fail_count``. A pass
    resets the counter so the threshold only trips on a streak.
    """
    state = _read_state(trw_dir)
    counters = _get_or_create(state, session_id)
    if passed:
        counters["build_check_fail_count"] = 0
    else:
        counters["build_check_fail_count"] += 1
    _write_state(trw_dir, state)


def record_unhandled_exception(session_id: str, trw_dir: Path) -> None:
    """Record one unhandled tool exception for the session."""
    state = _read_state(trw_dir)
    counters = _get_or_create(state, session_id)
    counters["unhandled_exception_count"] += 1
    _write_state(trw_dir, state)


def record_bug_learning(
    session_id: str, tags: list[str], trw_dir: Path
) -> None:
    """Record a bug-tagged learning entry.

    Increments ONLY when ``tags`` contains both ``bug`` and
    ``trw-internal``. Other tag combinations are intentionally ignored
    so the nudge fires on suspected internal bugs, not generic
    bug-finding work.
    """
    if "bug" not in tags or "trw-internal" not in tags:
        return
    state = _read_state(trw_dir)
    counters = _get_or_create(state, session_id)
    counters["bug_learning_count"] += 1
    _write_state(trw_dir, state)


def maybe_emit_feedback_nudge(
    session_id: str, trw_dir: Path, config: TRWConfig
) -> str | None:
    """Return the nudge text if armed, else ``None``.

    Returns ``None`` unconditionally when ``config.feedback.proactive``
    is ``False`` (NFR04 opt-in gate). Returns ``None`` after the first
    successful emission for a given ``session_id`` (per-session
    throttle). Otherwise emits when ANY counter meets its configured
    threshold and flips ``nudge_emitted`` to ``True`` before returning.
    """
    feedback = config.feedback
    if not feedback.proactive:
        return None
    state = _read_state(trw_dir)
    counters = state.get(session_id)
    if counters is None:
        return None
    if counters["nudge_emitted"]:
        return None
    armed = (
        counters["build_check_fail_count"] >= feedback.build_check_fail_threshold
        or counters["unhandled_exception_count"]
        >= feedback.unhandled_exception_threshold
        or counters["bug_learning_count"] >= feedback.bug_learning_threshold
    )
    if not armed:
        return None
    counters["nudge_emitted"] = True
    state[session_id] = counters
    _write_state(trw_dir, state)
    return FEEDBACK_NUDGE_TEXT


__all__ = [
    "FEEDBACK_NUDGE_TEXT",
    "maybe_emit_feedback_nudge",
    "record_build_check_outcome",
    "record_bug_learning",
    "record_unhandled_exception",
]
