"""State-assertion classifier for the FR05 validity-window nudge (PRD-CORE-244).

A learning that records an *invariant* stays true. A learning that records
*state* is true the day it is written and silently false later, and nothing on
the record marks which kind it is — ``expires`` was measured non-empty on 0 of
9,366 rows.

This module proposes a window. It never sets one. Silent stamping is prohibited
by the requirement rather than merely discouraged: a classifier that puts a TTL
on an invariant makes the store LESS true than one that puts none anywhere, and
this heuristic will misfire in both directions. So the author decides, and the
only output is advisory text.

NFR01: a pure string operation — no filesystem, no network, no config I/O beyond
the already-resolved ``TRWConfig`` the caller hands in.
"""

from __future__ import annotations

import re
from datetime import timedelta

__all__ = ["STATE_ASSERTION_MARKERS", "propose_validity_window", "validity_window_nudge"]

#: Phrases that mark a claim about *current* state rather than an invariant.
#: Each is matched case-insensitively on a word boundary.
_MARKER_PHRASES = (
    "currently",
    "not yet",
    "is now",
    "as of",
    "at present",
    "no longer",
)

#: A bare cardinal count ("7 rows", "3 callers") is the other state marker: a
#: number that was measured is a number that changes. Version-shaped and
#: date-shaped digits are deliberately excluded by requiring the digits to stand
#: alone as a word.
_BARE_CARDINAL = re.compile(r"(?<![\w.\-/])\d{1,3}(?:,\d{3})*(?![\w.\-/%])")

_MARKER_PATTERNS = tuple(re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE) for phrase in _MARKER_PHRASES)

#: Exposed for tests and for the tool docstring that explains the nudge.
STATE_ASSERTION_MARKERS = _MARKER_PHRASES


def propose_validity_window(
    summary: str,
    detail: str,
    learning_type: str,
    ttl_days_by_type: dict[str, int],
) -> timedelta | None:
    """Return a proposed validity window for a state-asserting learning.

    Args:
        summary: The learning's one-line summary.
        detail: The learning's full context.
        learning_type: The ``type`` field — ``convention`` and ``pattern``
            record invariants and are never given a window, whatever the text
            says.
        ttl_days_by_type: ``TRWConfig.state_learning_default_ttl_days`` — the
            per-type default window. A type absent from the table gets no
            proposal, which is how the invariant types opt out by configuration
            rather than by a hard-coded list.

    Returns:
        The proposed window, or ``None`` when the learning does not look like a
        state assertion (or its type records invariants).
    """
    ttl_days = ttl_days_by_type.get(learning_type.strip().lower())
    if not ttl_days:
        return None
    text = f"{summary}\n{detail}"
    if not any(pattern.search(text) for pattern in _MARKER_PATTERNS) and not _BARE_CARDINAL.search(text):
        return None
    return timedelta(days=ttl_days)


def validity_window_nudge(learning_id: str, window: timedelta) -> str:
    """Render the advisory text naming the window and the call that sets it.

    Naming the exact ``trw_learn_update`` call matters: ``expires`` was removed
    from the public ``trw_learn`` signature on 2026-07-28, so an author told only
    "consider setting a window" has no reachable way to act on it.
    """
    days = window.days
    return (
        f"This reads like a claim about current state, which is true today and silently "
        f"false later. If it is, give it a window ({days} days is the default for this "
        f"type): trw_learn_update(learning_id='{learning_id}', fields={{'expires': "
        f"'<YYYY-MM-DD>'}}). If it states a durable invariant instead, restate it as one "
        f"and ignore this."
    )
