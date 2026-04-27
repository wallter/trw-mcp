"""Tests for HPOTelemetryEvent base + subclasses — PRD-HPO-MEAS-001 FR-3."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trw_mcp.telemetry.event_base import (
    EVENT_TYPE_REGISTRY,
    CeremonyEvent,
    ContractEvent,
    DefaultResolutionError,
    H1ObserveModeWarning,
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

ALL_SUBCLASSES: list[type[HPOTelemetryEvent]] = [
    CeremonyEvent,
    ContractEvent,
    PhaseExposureEvent,
    ObserverEvent,
    MCPSecurityEvent,
    MetaTuneEvent,
    ThrashingEvent,
    LLMCallEvent,
    ToolCallEvent,
    HPOSessionStartEvent,
    HPOSessionEndEvent,
    HPOCeremonyComplianceEvent,
    H1ObserveModeWarning,
    SurfaceRegistered,
]


def test_hpo_telemetry_event_required_fields_session_id_and_emitter() -> None:
    # session_id + emitter + event_type are required on the raw base
    with pytest.raises(ValidationError):
        HPOTelemetryEvent()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        HPOTelemetryEvent(emitter="x", event_type="x")  # type: ignore[call-arg]
    ev = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert ev.session_id == "s1"
    assert ev.emitter == "x"


def test_hpo_telemetry_event_pydantic_strict_rejects_extras() -> None:
    with pytest.raises(ValidationError):
        HPOTelemetryEvent(
            session_id="s1",
            emitter="x",
            event_type="x",
            bogus_field="nope",  # type: ignore[call-arg]
        )


def test_hpo_telemetry_event_frozen_rejects_mutation() -> None:
    ev = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    with pytest.raises(ValidationError):
        ev.session_id = "s2"  # type: ignore[misc]


def test_event_id_autopopulates_with_evt_prefix_and_unique() -> None:
    a = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    b = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert a.event_id.startswith("evt_")
    assert b.event_id.startswith("evt_")
    assert a.event_id != b.event_id
    assert len(a.event_id) > len("evt_")


def test_ts_autopopulates_utc() -> None:
    ev = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert isinstance(ev.ts, datetime)
    assert ev.ts.tzinfo is not None
    assert ev.ts.utcoffset() == timezone.utc.utcoffset(ev.ts)


def test_surface_snapshot_id_empty_string_allowed_phase1() -> None:
    ev = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert ev.surface_snapshot_id == ""
    ev2 = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x", surface_snapshot_id="snap_123")
    assert ev2.surface_snapshot_id == "snap_123"


def test_payload_accepts_dict() -> None:
    ev = HPOTelemetryEvent(
        session_id="s1",
        emitter="x",
        event_type="x",
        payload={"k": "v", "n": 42},
    )
    assert ev.payload == {"k": "v", "n": 42}


def test_payload_default_is_empty_dict() -> None:
    ev = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert ev.payload == {}


def test_roundtrip_model_dump_json_validate_json() -> None:
    ev = CeremonyEvent(
        session_id="s1",
        run_id="r1",
        payload={"phase": "AARE"},
        parent_event_id="evt_parent",
        surface_snapshot_id="snap_a",
    )
    blob = ev.model_dump_json()
    restored = CeremonyEvent.model_validate_json(blob)
    assert restored == ev


def test_each_subclass_sets_unique_event_type() -> None:
    types = [cls(session_id="s1").event_type for cls in ALL_SUBCLASSES]
    assert len(types) == len(ALL_SUBCLASSES)
    assert len(set(types)) == len(types), f"duplicate event_types: {types}"


def test_each_subclass_defaults_emitter() -> None:
    for cls in ALL_SUBCLASSES:
        inst = cls(session_id="s1")
        assert inst.emitter, f"{cls.__name__} emitter empty"


def test_subclass_inherits_base_config_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        CeremonyEvent(session_id="s1", totally_new_field=1)  # type: ignore[call-arg]


def test_subclass_inherits_frozen() -> None:
    ev = LLMCallEvent(session_id="s1")
    with pytest.raises(ValidationError):
        ev.session_id = "other"  # type: ignore[misc]


def test_run_id_defaults_none_and_accepts_str() -> None:
    a = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x")
    assert a.run_id is None
    b = HPOTelemetryEvent(session_id="s1", emitter="x", event_type="x", run_id="r42")
    assert b.run_id == "r42"


# ---- PRD-HPO-MEAS-001 FR-9: H1ObserveModeWarning -------------------------


def test_h1_observe_mode_warning_is_observer_subclass() -> None:
    assert issubclass(H1ObserveModeWarning, ObserverEvent)
    assert issubclass(H1ObserveModeWarning, HPOTelemetryEvent)


def test_h1_observe_mode_warning_event_type() -> None:
    ev = H1ObserveModeWarning(session_id="s1")
    assert ev.event_type == "h1_observe_mode_warning"


def test_emit_h1_observe_mode_warning_factory_populates_required_payload() -> None:
    ev = emit_h1_observe_mode_warning(
        session_id="s1",
        run_id="r42",
        emitter_name="ceremony",
        fallback_reason="h1_substrate_not_live",
        buffered_event_count_since_start=7,
    )
    assert isinstance(ev, H1ObserveModeWarning)
    assert ev.session_id == "s1"
    assert ev.run_id == "r42"
    # FR-9 AC-1 required payload keys
    assert ev.payload["emitter_name"] == "ceremony"
    assert ev.payload["fallback_reason"] == "h1_substrate_not_live"
    assert ev.payload["buffered_event_count_since_start"] == 7
    assert ev.payload["activation_gate_blocked_reason"] == "h1_substrate_not_live"


def test_emit_h1_observe_mode_warning_frozen() -> None:
    ev = emit_h1_observe_mode_warning(
        session_id="s1",
        run_id=None,
        emitter_name="ceremony",
        fallback_reason="missing_consumer",
        buffered_event_count_since_start=0,
    )
    with pytest.raises(ValidationError):
        ev.session_id = "nope"  # type: ignore[misc]


# ---- PRD-HPO-MEAS-001 FR-13: EVENT_TYPE_REGISTRY + DefaultResolutionError ---


def test_event_type_registry_keys_match_all_subclasses() -> None:
    expected = {cls(session_id="s1").event_type for cls in ALL_SUBCLASSES}
    assert set(EVENT_TYPE_REGISTRY.keys()) == expected


def test_event_type_registry_values_are_correct_classes() -> None:
    for event_type, cls in EVENT_TYPE_REGISTRY.items():
        inst = cls(session_id="s1")
        assert inst.event_type == event_type, f"registry mismatch for {cls.__name__}"


def test_default_resolution_error_is_runtime_error() -> None:
    assert issubclass(DefaultResolutionError, RuntimeError)
    with pytest.raises(DefaultResolutionError):
        raise DefaultResolutionError("pricing.yaml missing")
