"""Unit tests for PRD-SCALE-001 decision_reversal_rate metric (FR11)."""

from __future__ import annotations

from trw_mcp.cognitive_scaling.metrics import (
    build_decision_reversal_event,
    compute_decision_reversal_rate,
)
from trw_mcp.telemetry.event_base import HPOTelemetryEvent


def test_reversal_rate_zero_decisions_is_zero() -> None:
    """FR11: no decisions -> 0.0 (no divide-by-zero)."""
    assert compute_decision_reversal_rate(decisions_total=0, decisions_reversed=0) == 0.0


def test_reversal_rate_fraction() -> None:
    """FR11: 2 of 4 reversed -> 0.5."""
    assert compute_decision_reversal_rate(decisions_total=4, decisions_reversed=2) == 0.5


def test_reversal_rate_clamps_overcount() -> None:
    """Reversed count cannot exceed total (clamp, never >1.0)."""
    assert compute_decision_reversal_rate(decisions_total=3, decisions_reversed=9) == 1.0


def test_build_event_is_unified_envelope_with_payload() -> None:
    """FR11: emitted via HPOTelemetryEvent envelope with metric in payload."""
    ev = build_decision_reversal_event(
        session_id="s1",
        run_id="r1",
        decisions_total=10,
        decisions_reversed=1,
        planning_mode="TRIANGULATED",
    )
    assert isinstance(ev, HPOTelemetryEvent)
    assert ev.payload["metric"] == "decision_reversal_rate"
    assert ev.payload["decision_reversal_rate"] == 0.1
    assert ev.payload["planning_mode"] == "TRIANGULATED"
    assert ev.session_id == "s1"
    assert ev.run_id == "r1"
