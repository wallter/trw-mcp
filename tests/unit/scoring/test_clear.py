"""Tests for scoring.clear — PRD-HPO-MEAS-001 FR-5."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from trw_mcp.scoring.clear import (
    ClearScore,
    compute,
)
from trw_mcp.telemetry.event_base import (
    ContractEvent,
    HPOCeremonyComplianceEvent,
    HPOSessionEndEvent,
    HPOSessionStartEvent,
    HPOTelemetryEvent,
    LLMCallEvent,
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


def _llm_call(*, session_id: str = "s1", usd: float = 0.001) -> LLMCallEvent:
    return LLMCallEvent(
        session_id=session_id,
        payload={"model": "claude-opus-4-7", "usd_cost_est": usd},
    )


def _session_start(ts: datetime) -> HPOSessionStartEvent:
    return HPOSessionStartEvent(session_id="s", ts=ts)


def _session_end(ts: datetime, *, outcome: str = "PASS") -> HPOSessionEndEvent:
    return HPOSessionEndEvent(session_id="s", ts=ts, payload={"outcome": outcome})


class TestClearScoreModel:
    def test_dimensions_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ClearScore(
                session_id="s",
                cost=1.5,  # out of range
                latency=1.0,
                efficacy=1.0,
                assurance=1.0,
                reliability=1.0,
                tool_call_count=0,
                total_usd_cost=0.0,
                total_wall_ms=0,
            )

    def test_frozen(self) -> None:
        s = ClearScore(
            session_id="s",
            cost=1.0,
            latency=1.0,
            efficacy=1.0,
            assurance=1.0,
            reliability=1.0,
            tool_call_count=0,
            total_usd_cost=0.0,
            total_wall_ms=0,
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
    def test_includes_tool_and_llm_costs(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(usd=0.25), _llm_call(usd=0.25)]
        score = compute("s", events, cost_p95_usd=1.0)
        assert score.cost == pytest.approx(0.5, abs=1e-6)

    def test_cost_clamped_at_zero_when_over_p95(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(usd=5.0)]
        score = compute("s", events)  # default p95 2.00
        assert score.cost == 0.0


class TestLatencyDimension:
    def test_uses_session_start_end_wall_clock(self) -> None:
        start = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(seconds=5)
        events: list[HPOTelemetryEvent] = [
            _session_start(start),
            _tool_call(wall_ms=100),
            _tool_call(wall_ms=200),
            _session_end(end),
        ]
        score = compute("s", events, latency_p95_ms=10_000)
        assert score.latency == pytest.approx(0.5, abs=1e-6)

    def test_latency_clamped_at_zero_when_over_p95(self) -> None:
        start = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(seconds=500)
        events: list[HPOTelemetryEvent] = [_session_start(start), _session_end(end)]
        score = compute("s", events, latency_p95_ms=120_000)
        assert score.latency == 0.0


class TestEfficacyDimension:
    def test_uses_ceremony_compliance_pass_fail(self) -> None:
        events: list[HPOTelemetryEvent] = [_tool_call(outcome="success") for _ in range(7)] + [
            HPOCeremonyComplianceEvent(
                session_id="s",
                payload={"compliant": False, "violations": ["missing_build"]},
            )
        ]
        score = compute("s", events)
        assert score.efficacy == 0.0

    def test_falls_back_to_session_end_outcome(self) -> None:
        events: list[HPOTelemetryEvent] = [_session_end(datetime.now(tz=timezone.utc), outcome="PASS")]
        score = compute("s", events)
        assert score.efficacy == 1.0


class TestAssuranceDimension:
    def test_uses_contract_schema_valid_rate(self) -> None:
        events: list[HPOTelemetryEvent] = [
            ContractEvent(session_id="s", payload={"schema_valid": True}),
            ContractEvent(session_id="s", payload={"schema_valid": False}),
            ContractEvent(session_id="s", payload={"schema_valid": True}),
        ]
        score = compute("s", events)
        assert score.assurance == pytest.approx(2 / 3, abs=1e-6)

    def test_missing_assurance_signals_defaults_to_zero(self) -> None:
        score = compute("s", [_tool_call(outcome="success")])
        assert score.assurance == 0.0


class TestReliabilityDimension:
    def test_retry_and_error_outcomes_lower_reliability(self) -> None:
        events: list[HPOTelemetryEvent] = [
            _tool_call(outcome="success"),
            _tool_call(outcome="retry"),
            _tool_call(outcome="error"),
        ]
        score = compute("s", events)
        assert score.reliability == pytest.approx(1 / 3, abs=1e-6)

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

    def test_session_events_dont_count_as_tool_calls(self) -> None:
        events: list[HPOTelemetryEvent] = [
            _session_start(datetime.now(tz=timezone.utc)),
            _session_end(datetime.now(tz=timezone.utc)),
        ]
        score = compute("s", events)
        assert score.tool_call_count == 0
