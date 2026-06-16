"""FR-09 — ProbeEvent telemetry via the unified HPOTelemetryEvent (PRD-CORE-144)."""

from __future__ import annotations

from datetime import datetime, timezone

from trw_mcp.models.probe import ProbeEvidence, ProbeResult
from trw_mcp.probe.telemetry import ProbeEvent, build_probe_event
from trw_mcp.telemetry.event_base import HPOTelemetryEvent


def _result(verdict: str = "refutes") -> ProbeResult:
    return ProbeResult(
        hypothesis="h",
        hypothesis_id="H1",
        verdict=verdict,  # type: ignore[arg-type]
        evidence=ProbeEvidence(wall_ms=8234, timed_out=False),
        confidence=0.9,
        ts=datetime(2026, 4, 16, tzinfo=timezone.utc),
        run_id="run-1",
    )


def test_probe_event_is_hpo_telemetry_variant() -> None:
    # FR-09 A1: ProbeEvent is a variant of the unified telemetry envelope.
    event = build_probe_event(_result(), session_id="run-1", planning_mode="TRIANGULATED_WITH_PROBE")
    assert isinstance(event, HPOTelemetryEvent)
    assert isinstance(event, ProbeEvent)
    assert event.event_type == "probe"
    assert event.emitter == "probe_harness"


def test_decisive_verdict_flagged_for_yield_metric() -> None:
    # probe yield numerator = decisive (supports/refutes) verdicts.
    refutes = build_probe_event(_result("refutes"), session_id="s", planning_mode="m")
    assert refutes.payload["decisive"] is True
    inconclusive = build_probe_event(_result("inconclusive"), session_id="s", planning_mode="m")
    assert inconclusive.payload["decisive"] is False


def test_event_payload_carries_probe_fields() -> None:
    event = build_probe_event(_result(), session_id="run-1", planning_mode="DUAL_DRAFT")
    assert event.payload["verdict"] == "refutes"
    assert event.payload["hypothesis_id"] == "H1"
    assert event.payload["wall_ms"] == 8234
    assert event.payload["planning_mode"] == "DUAL_DRAFT"
    assert event.run_id == "run-1"
    assert event.session_id == "run-1"


def test_event_is_frozen_extra_forbid() -> None:
    # Inherits HPOTelemetryEvent's frozen + extra=forbid contract.
    event = build_probe_event(_result(), session_id="s", planning_mode="m")
    assert event.model_config["frozen"] is True
