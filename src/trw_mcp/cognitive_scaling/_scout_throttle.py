"""Scout anti-inflation throttle — PRD-SCALE-001 FR14.

Belongs to the ``cognitive_scaling`` package facade; ``scout.py`` re-exports
``evaluate_throttle``.

Sprint-97 scope: the throttle is SCAFFOLDED (per sprint exit criteria —
"FR-14 anti-inflation throttle scaffolded (rolling 30-day, 15% Mode-3 cap;
enforcement live Sprint 98)"). The decision function here is pure + fully
tested; the live auto-tightening of thresholds is a Sprint-98 enforcement
step. The function computes the rolling Mode-3 escalation rate and reports
whether the cap is exceeded so the caller can emit a warning.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.models.cognitive_scaling import PlanningMode


class ThrottleDecision(BaseModel):
    """Result of evaluating the rolling Mode-3 escalation rate (FR14)."""

    model_config = ConfigDict(extra="forbid")

    mode3_rate: float = Field(ge=0.0, le=1.0)
    cap: float = Field(ge=0.0, le=1.0)
    over_cap: bool = False
    sample_size: int = Field(ge=0)
    #: Sprint-97 scaffold flag: enforcement (threshold auto-tighten) is live
    #: in Sprint 98. When ``over_cap`` is True the caller emits a warning only.
    enforcement_active: bool = False


def evaluate_throttle(
    recent_modes: Sequence[PlanningMode | int],
    *,
    cap: float = 0.15,
) -> ThrottleDecision:
    """Compute the rolling TRIANGULATED_WITH_PROBE escalation rate (FR14).

    ``recent_modes`` is the rolling-window classification stream (caller owns
    the 30-day windowing). An empty stream is never "over cap" (no inflation
    can be inferred from zero samples). Pure + deterministic.
    """
    n = len(recent_modes)
    if n == 0:
        return ThrottleDecision(mode3_rate=0.0, cap=cap, over_cap=False, sample_size=0)
    mode3 = sum(1 for m in recent_modes if int(m) == int(PlanningMode.TRIANGULATED_WITH_PROBE))
    rate = mode3 / n
    return ThrottleDecision(
        mode3_rate=rate,
        cap=cap,
        over_cap=rate > cap,
        sample_size=n,
    )


__all__ = ["ThrottleDecision", "evaluate_throttle"]
