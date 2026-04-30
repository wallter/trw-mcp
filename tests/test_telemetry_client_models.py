"""Tests for telemetry models and record serialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tests._telemetry_client_support import _INSTALL_ID, _FW_VERSION, _base_event
from trw_mcp.telemetry.client import _event_to_record
from trw_mcp.telemetry.models import (
    CeremonyComplianceEvent,
    SessionEndEvent,
    SessionStartEvent,
    ToolInvocationEvent,
)


class TestTelemetryEventBase:
    def test_timestamp_defaults_to_utc_now(self) -> None:
        before = datetime.now(tz=timezone.utc)
        event = _base_event()
        after = datetime.now(tz=timezone.utc)
        assert before <= event.timestamp <= after

    def test_timestamp_is_timezone_aware(self) -> None:
        event = _base_event()
        assert event.timestamp.tzinfo is not None

    def test_required_fields_present(self) -> None:
        event = _base_event()
        assert event.installation_id == _INSTALL_ID
        assert event.framework_version == _FW_VERSION
        assert event.event_type == "test_event"

    def test_explicit_timestamp_accepted(self) -> None:
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event = _base_event(timestamp=ts)
        assert event.timestamp == ts

    def test_model_dump_serializable(self) -> None:
        event = _base_event()
        record = event.model_dump()
        assert "timestamp" in record
        assert "installation_id" in record
        assert "event_type" in record


class TestSessionStartEvent:
    def test_default_event_type(self) -> None:
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
        )
        assert event.event_type == "session_start"

    def test_optional_run_id_defaults_none(self) -> None:
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
        )
        assert event.run_id is None

    def test_run_id_can_be_set(self) -> None:
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            run_id="run-xyz",
        )
        assert event.run_id == "run-xyz"

    def test_learnings_loaded_defaults_zero(self) -> None:
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
        )
        assert event.learnings_loaded == 0

    def test_learnings_loaded_can_be_set(self) -> None:
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            learnings_loaded=42,
        )
        assert event.learnings_loaded == 42


class TestToolInvocationEvent:
    def test_default_event_type(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_checkpoint",
        )
        assert event.event_type == "tool_invocation"

    def test_duration_ms_defaults_zero(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_checkpoint",
        )
        assert event.duration_ms == 0

    def test_success_defaults_true(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_checkpoint",
        )
        assert event.success is True

    def test_phase_defaults_empty_string(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_checkpoint",
        )
        assert event.phase == ""

    def test_all_fields_set(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_deliver",
            duration_ms=250,
            success=False,
            phase="deliver",
        )
        assert event.tool_name == "trw_deliver"
        assert event.duration_ms == 250
        assert event.success is False
        assert event.phase == "deliver"


class TestCeremonyComplianceEvent:
    def test_default_event_type(self) -> None:
        event = CeremonyComplianceEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            run_id="run-abc",
        )
        assert event.event_type == "ceremony_compliance"

    def test_phases_completed_defaults_empty(self) -> None:
        event = CeremonyComplianceEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            run_id="run-abc",
        )
        assert event.phases_completed == []

    def test_phases_completed_can_be_set(self) -> None:
        event = CeremonyComplianceEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            run_id="run-abc",
            phases_completed=["research", "plan", "implement"],
        )
        assert event.phases_completed == ["research", "plan", "implement"]

    def test_score_defaults_zero(self) -> None:
        event = CeremonyComplianceEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            run_id="run-abc",
        )
        assert event.score == 0


class TestSessionEndEvent:
    def test_default_event_type(self) -> None:
        event = SessionEndEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
        )
        assert event.event_type == "session_end"

    def test_defaults(self) -> None:
        event = SessionEndEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
        )
        assert event.total_duration_ms == 0
        assert event.tools_invoked == 0
        assert event.ceremony_score == 0

    def test_fields_settable(self) -> None:
        event = SessionEndEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            total_duration_ms=5000,
            tools_invoked=12,
            ceremony_score=85,
        )
        assert event.total_duration_ms == 5000
        assert event.tools_invoked == 12
        assert event.ceremony_score == 85


class TestEventToRecord:
    def test_returns_dict(self) -> None:
        event = _base_event()
        record = _event_to_record(event)
        assert isinstance(record, dict)

    def test_timestamp_serialized_as_string(self) -> None:
        event = _base_event()
        record = _event_to_record(event)
        assert isinstance(record["timestamp"], str)

    def test_record_is_json_serializable(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_learn",
            duration_ms=100,
        )
        record = _event_to_record(event)
        serialized = json.dumps(record)
        parsed = json.loads(serialized)
        assert parsed["tool_name"] == "trw_learn"

    def test_all_model_fields_present(self) -> None:
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            run_id="run-xyz",
            learnings_loaded=5,
        )
        record = _event_to_record(event)
        assert "installation_id" in record
        assert "framework_version" in record
        assert "event_type" in record
        assert "run_id" in record
        assert "learnings_loaded" in record
