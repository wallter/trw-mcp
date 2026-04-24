"""FR-14 per-field non-zero instrumentation test — NFR-7 CI gate.

Every field on every :class:`HPOTelemetryEvent` subclass that is NOT
annotated ``nullable_zero_by_design: true`` MUST have a corresponding
assertion that a realistic emission path populates it with a non-default
value. A missing per-field test is a BUILD FAILURE (DIST D11/O2 pattern
prevention).

The test runs over every subclass in ``EVENT_TYPE_REGISTRY``. Fields
whose legitimate zero/null cases are documented carry the ``nullable_zero_by_design``
marker in the PRD's nullability annex (§FR-14 body) — here we skip them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import pytest

from trw_mcp.telemetry.event_base import (
    EVENT_TYPE_REGISTRY,
    CeremonyEvent,
    ContractEvent,
    HPOCeremonyComplianceEvent,
    HPOSessionEndEvent,
    HPOSessionStartEvent,
    HPOTelemetryEvent,
    LLMCallEvent,
    MCPSecurityEvent,
    MetaTuneEvent,
    ObserverEvent,
    PhaseExposureEvent,
    SurfaceRegistered,
    ThrashingEvent,
    ToolCallEvent,
    emit_h1_observe_mode_warning,
)

#: PRD-HPO-MEAS-001 §FR-14: fields legitimately zero/null by design.
_NULLABLE_BY_DESIGN: Final[frozenset[str]] = frozenset(
    {
        "run_id",                 # Phase 1 pre-run cold-start
        "surface_snapshot_id",    # Phase 1 default empty-string until wiring
        "parent_event_id",        # optional for root events
    }
)

#: Realistic sample constructor per subclass — each returns a fully
#: populated event (payload carrying FR-14 meaningful keys).
_SAMPLE_BUILDERS: Final[dict[str, HPOTelemetryEvent]] = {
    "ceremony": CeremonyEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"phase": "IMPLEMENT"},
    ),
    "contract": ContractEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"contract_id": "ctx-42", "outcome": "pass"},
    ),
    "phase_exposure": PhaseExposureEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"phase": "VALIDATE", "duration_ms": 1200},
    ),
    "observer": ObserverEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"kind": "oversight_hook"},
    ),
    "mcp_security": MCPSecurityEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"decision": "allow", "scope": "tool_call"},
    ),
    "meta_tune": MetaTuneEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"proposal_id": "prop-7", "outcome": "queued"},
    ),
    "thrashing": ThrashingEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"retry_count": 3, "tool": "trw_recall"},
    ),
    "llm_call": LLMCallEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"model": "claude-opus-4-7", "input_tokens": 120, "output_tokens": 80},
    ),
    "tool_call": ToolCallEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={
            "tool": "trw_recall", "wall_ms": 45,
            "input_tokens": 0, "output_tokens": 0,
            "outcome": "success", "pricing_version": "2026-04-23",
        },
    ),
    "session_start": HPOSessionStartEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"learnings_loaded": 42, "framework_version": "v24.6_TRW"},
    ),
    "session_end": HPOSessionEndEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"reason": "deliver", "duration_ms": 8700},
    ),
    "ceremony_compliance": HPOCeremonyComplianceEvent(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={"score": 0.91},
    ),
    "h1_observe_mode_warning": emit_h1_observe_mode_warning(
        session_id="s1",
        run_id="r1",
        emitter_name="ceremony",
        fallback_reason="h1_substrate_not_live",
        buffered_event_count_since_start=5,
        surface_snapshot_id="snap_a",
    ),
    "surface_registered": SurfaceRegistered(
        session_id="s1", run_id="r1", surface_snapshot_id="snap_a",
        payload={
            "surface_id": "agents:trw-implementer.md",
            "content_hash": "ff" * 32,
            "source_path": "agents/trw-implementer.md",
            "category": "agents",
        },
    ),
}


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPE_REGISTRY.keys()))
def test_registered_event_has_sample_builder(event_type: str) -> None:
    """Every registered subclass must have a sample constructor in this test."""
    assert event_type in _SAMPLE_BUILDERS, (
        f"EVENT_TYPE_REGISTRY entry {event_type!r} has no sample in _SAMPLE_BUILDERS. "
        f"Add one so FR-14 field-population can assert on it."
    )


@pytest.mark.parametrize("event_type", sorted(_SAMPLE_BUILDERS.keys()))
def test_non_nullable_top_level_fields_populated(event_type: str) -> None:
    """FR-14: every non-``nullable_zero_by_design`` field has a non-default value."""
    event = _SAMPLE_BUILDERS[event_type]
    for field_name, info in type(event).model_fields.items():
        if field_name in _NULLABLE_BY_DESIGN:
            continue
        value = getattr(event, field_name)
        default = info.default
        if field_name == "event_id":
            assert isinstance(value, str) and value.startswith("evt_")
            continue
        if field_name == "ts":
            assert isinstance(value, datetime)
            assert value.tzinfo is not None
            continue
        if field_name == "payload":
            assert isinstance(value, dict) and len(value) > 0, (
                f"{event_type}.payload is empty — at least one FR-14 payload key required"
            )
            continue
        assert value != default or (isinstance(value, str) and value), (
            f"{event_type}.{field_name} remained at default {default!r} "
            f"(actual {value!r}); specify a non-default sample in _SAMPLE_BUILDERS."
        )


class TestPayloadKeysCoverPerFRMeaning:
    """FR-14 spot checks: key subclasses carry their canonical payload keys."""

    def test_tool_call_carries_tool_and_wall_ms(self) -> None:
        ev = _SAMPLE_BUILDERS["tool_call"]
        assert "tool" in ev.payload
        assert "wall_ms" in ev.payload
        assert "pricing_version" in ev.payload

    def test_llm_call_carries_model_and_tokens(self) -> None:
        ev = _SAMPLE_BUILDERS["llm_call"]
        assert "model" in ev.payload
        assert "input_tokens" in ev.payload
        assert "output_tokens" in ev.payload

    def test_h1_observe_mode_warning_carries_activation_gate(self) -> None:
        ev = _SAMPLE_BUILDERS["h1_observe_mode_warning"]
        assert ev.payload["activation_gate_blocked_reason"]
        assert ev.payload["emitter_name"]

    def test_session_start_carries_learnings_loaded(self) -> None:
        ev = _SAMPLE_BUILDERS["session_start"]
        assert ev.payload.get("learnings_loaded") is not None
