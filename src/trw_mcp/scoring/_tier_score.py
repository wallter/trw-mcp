"""Tier-aware ceremony scoring (PRD-CORE-060-FR03).

Belongs to the ``_complexity.py`` facade. Re-exported there (and from
``trw_mcp.scoring``) for back-compat, so existing
``from trw_mcp.scoring._complexity import compute_tier_ceremony_score`` and
``from trw_mcp.scoring import compute_tier_ceremony_score`` imports continue
to work.

Splits the tier expectation table and event-detection helpers out of
``_complexity.py`` to keep both Modules under the 350-line gate. The
classification side (``classify_complexity`` etc.) stays in ``_complexity.py``;
this Module owns the orthogonal concern of scoring an event stream against a
tier's expected ceremony.
"""

from __future__ import annotations


class _TierExpectation:
    """Expected ceremony events and scoring rules for a complexity tier."""

    __slots__ = (
        "checkpoint_min",
        "events",
        "missing_review_penalty",
        "review_bonus",
        "review_mandatory",
    )

    def __init__(
        self,
        events: frozenset[str],
        checkpoint_min: int,
        review_mandatory: bool,
        review_bonus: int,
        missing_review_penalty: int,
    ) -> None:
        self.events = events
        self.checkpoint_min = checkpoint_min
        self.review_mandatory = review_mandatory
        self.review_bonus = review_bonus
        self.missing_review_penalty = missing_review_penalty


__all__ = [
    "_TierExpectation",
]
