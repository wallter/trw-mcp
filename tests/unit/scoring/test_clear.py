"""Tests for scoring.clear — PRD-HPO-MEAS-001 FR-5."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.scoring.clear import (
    ClearScore,
    compute,
)
from trw_mcp.telemetry.event_base import (
    CeremonyEvent,
    ContractEvent,
    HPOTelemetryEvent,
    ThrashingEvent,
    ToolCallEvent,
)


def _tool_call(
    *,
    session_id: str = "s1",
    outcome: str = "success",
    wall_ms: int = 100,
    usd: float = 0.001,
    event_id: str | None = None,
) -> ToolCallEvent:
    kwargs: dict[str, object] = {
        "session_id": session_id,
        "payload": {
            "tool": "trw_recall",
            "outcome": outcome,
            "wall_ms": wall_ms,
            "usd_cost_est": usd,
        },
    }
    if event_id is not None:
        kwargs["event_id"] = event_id
    return ToolCallEvent(**kwargs)  # type: ignore[arg-type]


class TestClearScoreModel:
    def test_dimensions_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ClearScore(
                session_id="s",
                cost=1.5,  # out of range
                latency=1.0, efficacy=1.0, assurance=1.0, reliability=1.0,
                tool_call_count=0, total_usd_cost=0.0, total_wall_ms=0,
            )

    def test_frozen(self) -> None:
        s = ClearScore(
            session_id="s", cost=1.0, latency=1.0, efficacy=1.0,
            assurance=1.0, reliability=1.0,
            tool_call_count=0, total_usd_cost=0.0, total_wall_ms=0,
        )
        with pytest.raises(ValidationError):
            s.cost = 0.5  # type: ignore[misc]


class TestComputeEmptySession:
    def test_empty_events_produce_valid_score(self) -> None:
        score = compute("s1", [])
        assert score.session_id == "s1"
        assert score.tool_call_count == 0
        # Cost + latency dims are 1.0 because zero usage = max-efficient.
        assert score.cost == 1.0
        assert score.latency == 1.0
        # Efficacy + assurance 0 because no outcomes observed.
        assert score.efficacy == 0.0
        # Reliability is 1 when no calls exist (nothing to thrash).
        assert score.reliability == 1.0


class TestCostDimension:
    def test_low_cost_high_score(self) -> None:
        events: list[HPOTelemetryEvent] = [
            _tool_call(usd=0.01) for _ in range(3)
        ]
        score = compute("s", events)
        # 0.03 USD / 2.00 p95 → cost = 1 - 0.015 = 0.985
        assert score.cost == pytest.approx(0.985, abs=1e-3)

    def test_cost_clamped_at_zero_when_over_p95(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(usd=5.0)]
        score = compute("s", events)  # default p95 2.00
        assert score.cost == 0.0


class TestLatencyDimension:
    def test_fast_session_high_score(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(wall_ms=1000)]
        score = compute("s", events)
        # 1000 / 120_000 → latency = 1 - ~0.0083 = 0.9917
        assert score.latency == pytest.approx(0.9917, abs=1e-3)

    def test_latency_clamped_at_zero_when_over_p95(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(wall_ms=500_000)]
        score = compute("s", events)
        assert score.latency == 0.0


class TestEfficacyDimension:
    def test_uses_ceremony_score_when_provided(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(outcome="error")] * 10
        # Even though all tool calls errored, ceremony_compliance_score
        # overrides the fallback.
        score = compute("s", events, ceremony_compliance_score=0.75)
        assert score.efficacy == 0.75

    def test_falls_back_to_success_ratio(self) -> None:
        events: list[HPOTelemetryEvent] = [
            _tool_call(outcome="success") for _ in range(7)
        ] + [_tool_call(outcome="error") for _ in range(3)]
        score = compute("s", events)
        assert score.efficacy == pytest.approx(0.7, abs=1e-6)


class TestAssuranceDimension:
    def test_contract_paired_calls_lift_assurance(self) -> None:
        tc1 = _tool_call(event_id="evt_tool_1")
        tc2 = _tool_call(event_id="evt_tool_2")
        # Pair a ContractEvent with tc1 — assurance = 1/2 = 0.5
        contract = ContractEvent(
            session_id="s",
            parent_event_id="evt_tool_1",
            payload={"contract_id": "c1", "outcome": "pass"},
        )
        score = compute("s", [tc1, tc2, contract])
        assert score.assurance == pytest.approx(0.5, abs=1e-6)

    def test_falls_back_to_success_ratio_when_no_contracts(self) -> None:
        events: list[HPOTelemetryEvent] = [
            _tool_call(outcome="success") for _ in range(8)
        ] + [_tool_call(outcome="error") for _ in range(2)]
        score = compute("s", events)
        # No contract events → assurance = success_ratio = 0.8
        assert score.assurance == pytest.approx(0.8, abs=1e-6)


class TestReliabilityDimension:
    def test_thrashing_bursts_lower_reliability(self) -> None:
        tc1 = _tool_call(event_id="evt_tool_a")
        tc2 = _tool_call(event_id="evt_tool_b")
        thrashing = ThrashingEvent(
            session_id="s",
            parent_event_id="evt_tool_a",
            payload={"retry_count": 5},
        )
        score = compute("s", [tc1, tc2, thrashing])
        # 1 burst / 2 calls → reliability = 1 - 0.5 = 0.5
        assert score.reliability == pytest.approx(0.5, abs=1e-6)

    def test_no_thrashing_yields_full_reliability(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call() for _ in range(5)]
        score = compute("s", events)
        assert score.reliability == 1.0


class TestTotalRollups:
    def test_aggregates_costs_and_wall_ms(self) -> None:
        events: list[HPOTelemetryEvent] = [
            _tool_call(wall_ms=100, usd=0.01),
            _tool_call(wall_ms=200, usd=0.02),
            _tool_call(wall_ms=300, usd=0.03),
        ]
        score = compute("s", events)
        assert score.tool_call_count == 3
        assert score.total_wall_ms == 600
        assert score.total_usd_cost == pytest.approx(0.06, abs=1e-6)


class TestMalformedPayloads:
    def test_non_numeric_wall_ms_ignored(self) -> None:
        ev = ToolCallEvent(
            session_id="s",
            payload={"tool": "x", "outcome": "success", "wall_ms": "not-a-number"},
        )
        score = compute("s", [ev])
        # wall_ms=0 contributed; still a valid score.
        assert score.tool_call_count == 1
        assert score.total_wall_ms == 0

    def test_ceremony_events_dont_count_as_tool_calls(self) -> None:
        events: list[HPOTelemetryEvent] = [
            CeremonyEvent(session_id="s", payload={"phase": "IMPLEMENT"}),
            CeremonyEvent(session_id="s", payload={"phase": "DELIVER"}),
        ]
        score = compute("s", events)
        assert score.tool_call_count == 0
