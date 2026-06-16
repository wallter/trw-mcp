"""Cognitive-scaling metrics emission — PRD-SCALE-001 FR11.

Belongs to the ``cognitive_scaling`` package facade.

``build_decision_reversal_event`` constructs the ``decision_reversal_rate``
telemetry event in the unified ``HPOTelemetryEvent`` envelope (PRD-HPO-MEAS-001
— no legacy emitter path for new code, per Sprint-97 cross-PRD check). The
event is payload-backed (the base class is ``extra=forbid``), so the metric +
its inputs live in ``payload``. The caller routes the returned event through
the telemetry pipeline; this factory keeps the payload shape consistent.

Sprint-97 scope: the EVENT FACTORY is live + tested. Wiring the per-run
reversal computation into the DELIVER phase is the Sprint-98 step (the rate
input is supplied by the caller here).
"""

from __future__ import annotations

import structlog

from trw_mcp.telemetry.event_base import ObserverEvent

logger = structlog.get_logger(__name__)

#: Canonical payload keys for the decision_reversal_rate metric (FR11). Kept
#: here so emitters and consumers agree on the shape.
DECISION_REVERSAL_PAYLOAD_KEYS: tuple[str, ...] = (
    "metric",
    "decision_reversal_rate",
    "decisions_total",
    "decisions_reversed",
    "planning_mode",
)


def compute_decision_reversal_rate(*, decisions_total: int, decisions_reversed: int) -> float:
    """Fraction of plan decisions reversed during IMPLEMENT/VALIDATE (FR11).

    A run with zero recorded decisions has a reversal rate of 0.0 (nothing
    could be reversed) — never a divide-by-zero.
    """
    if decisions_total <= 0:
        return 0.0
    reversed_clamped = max(0, min(decisions_reversed, decisions_total))
    return reversed_clamped / decisions_total


def build_decision_reversal_event(
    *,
    session_id: str,
    run_id: str | None,
    decisions_total: int,
    decisions_reversed: int,
    planning_mode: str,
    surface_snapshot_id: str = "",
) -> ObserverEvent:
    """Build the ``decision_reversal_rate`` H1 telemetry event (FR11).

    Returns an ``ObserverEvent`` (the unified envelope) carrying the metric in
    ``payload``. Not auto-published — the caller routes it through the
    telemetry pipeline at DELIVER time.
    """
    rate = compute_decision_reversal_rate(decisions_total=decisions_total, decisions_reversed=decisions_reversed)
    event = ObserverEvent(
        session_id=session_id,
        run_id=run_id,
        surface_snapshot_id=surface_snapshot_id,
        payload={
            "metric": "decision_reversal_rate",
            "decision_reversal_rate": rate,
            "decisions_total": decisions_total,
            "decisions_reversed": decisions_reversed,
            "planning_mode": planning_mode,
        },
    )
    logger.info(
        "decision_reversal_rate_emitted",
        component="cognitive_scaling.metrics",
        op="emit",
        outcome="ok",
        run_id=run_id,
        decision_reversal_rate=rate,
        planning_mode=planning_mode,
    )
    return event


__all__ = [
    "DECISION_REVERSAL_PAYLOAD_KEYS",
    "build_decision_reversal_event",
    "compute_decision_reversal_rate",
]
