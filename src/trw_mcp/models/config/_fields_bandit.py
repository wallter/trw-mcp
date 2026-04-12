"""Bandit-based learning selection configuration fields (PRD-CORE-105)."""

from __future__ import annotations

from pydantic import Field


class _BanditFields:
    """Bandit selection domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Bandit-based nudge selection (PRD-CORE-105-FR06) --

    phase_transition_withhold_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=0.30,
        description=(
            "Fraction of non-critical learnings withheld at phase boundaries "
            "for micro-randomized causal signal (FR06). Range: [0.0, 0.30]."
        ),
    )
