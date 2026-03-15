"""Tests for trw_mcp.telemetry models and TelemetryClient — PRD-CORE-031.

Does NOT duplicate coverage from test_telemetry_anonymizer.py.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.telemetry.client import TelemetryClient, _event_to_record
from trw_mcp.telemetry.models import (
    CeremonyComplianceEvent,
    SessionEndEvent,
    SessionStartEvent,
    TelemetryEvent,
    ToolInvocationEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INSTALL_ID = "abcd1234abcd1234"
_FW_VERSION = "v21.0_TRW"


def _base_event(**kwargs: object) -> TelemetryEvent:
    return TelemetryEvent(
        installation_id=_INSTALL_ID,
        framework_version=_FW_VERSION,
        event_type="test_event",
        **kwargs,  # type: ignore[arg-type]
    )


# ===========================================================================
# TelemetryEvent — base model
# ===========================================================================


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
        # model_dump includes datetime objects; _event_to_record converts them
        assert "timestamp" in record
        assert "installation_id" in record
        assert "event_type" in record


# ===========================================================================
# SessionStartEvent
# ===========================================================================


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


# ===========================================================================
# ToolInvocationEvent
# ===========================================================================


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


# ===========================================================================
# CeremonyComplianceEvent
# ===========================================================================


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


# ===========================================================================
# SessionEndEvent
# ===========================================================================


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


# ===========================================================================
# _event_to_record — JSONL serialization helper
# ===========================================================================


class TestEventToRecord:
    def test_returns_dict(self) -> None:
        event = _base_event()
        record = _event_to_record(event)
        assert isinstance(record, dict)

    def test_timestamp_serialized_as_string(self) -> None:
        event = _base_event()
        record = _event_to_record(event)
        # After coercion, timestamp should be a string (ISO format)
        assert isinstance(record["timestamp"], str)

    def test_record_is_json_serializable(self) -> None:
        event = ToolInvocationEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            tool_name="trw_learn",
            duration_ms=100,
        )
        record = _event_to_record(event)
        # Should not raise
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


# ===========================================================================
# TelemetryClient — disabled client
# ===========================================================================


class TestTelemetryClientDisabled:
    def _make_client(self, tmp_path: Path) -> TelemetryClient:
        return TelemetryClient(
            enabled=False,
            output_path=tmp_path / "telemetry.jsonl",
        )

    def test_record_event_is_noop(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        assert client.queue_size() == 0

    def test_flush_returns_zero(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        result = client.flush()
        assert result == 0

    def test_flush_does_not_create_file(self, tmp_path: Path) -> None:
        output = tmp_path / "telemetry.jsonl"
        client = TelemetryClient(enabled=False, output_path=output)
        client.flush()
        assert not output.exists()

    def test_enabled_property_is_false(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        assert client.enabled is False

    def test_queue_size_always_zero(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        assert client.queue_size() == 0


# ===========================================================================
# TelemetryClient — enabled client
# ===========================================================================


class TestTelemetryClientEnabled:
    def _make_client(self, tmp_path: Path) -> TelemetryClient:
        output = tmp_path / "logs" / "telemetry.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        return TelemetryClient(enabled=True, output_path=output)

    def test_enabled_property_is_true(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        assert client.enabled is True

    def test_record_event_increments_queue(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        assert client.queue_size() == 1

    def test_multiple_events_accumulate(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        for _ in range(5):
            client.record_event(_base_event())
        assert client.queue_size() == 5

    def test_flush_returns_event_count(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        client.record_event(_base_event())
        written = client.flush()
        assert written == 2

    def test_flush_clears_queue(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        client.flush()
        assert client.queue_size() == 0

    def test_flush_writes_jsonl_file(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        event = SessionStartEvent(
            installation_id=_INSTALL_ID,
            framework_version=_FW_VERSION,
            learnings_loaded=3,
        )
        client.record_event(event)
        client.flush()
        output = tmp_path / "logs" / "telemetry.jsonl"
        assert output.exists()
        lines = [l for l in output.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_type"] == "session_start"
        assert record["learnings_loaded"] == 3

    def test_flush_appends_multiple_lines(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(
            ToolInvocationEvent(
                installation_id=_INSTALL_ID,
                framework_version=_FW_VERSION,
                tool_name="trw_init",
            )
        )
        client.record_event(
            SessionEndEvent(
                installation_id=_INSTALL_ID,
                framework_version=_FW_VERSION,
                tools_invoked=1,
            )
        )
        client.flush()
        output = tmp_path / "logs" / "telemetry.jsonl"
        lines = [l for l in output.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_flush_timestamps_are_strings(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        client.flush()
        output = tmp_path / "logs" / "telemetry.jsonl"
        record = json.loads(output.read_text().strip())
        assert isinstance(record["timestamp"], str)

    def test_flush_idempotency_second_flush_returns_zero(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        client.record_event(_base_event())
        client.flush()
        result = client.flush()
        assert result == 0

    def test_flush_empty_queue_returns_zero(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        result = client.flush()
        assert result == 0


# ===========================================================================
# TelemetryClient — thread safety
# ===========================================================================


class TestTelemetryClientThreadSafety:
    def test_concurrent_record_event_does_not_corrupt_queue(self, tmp_path: Path) -> None:
        output = tmp_path / "telemetry.jsonl"
        client = TelemetryClient(enabled=True, output_path=output)
        n_threads = 20
        n_events_each = 50
        errors: list[Exception] = []

        def _record() -> None:
            try:
                for _ in range(n_events_each):
                    client.record_event(_base_event())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_record) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert client.queue_size() == n_threads * n_events_each

    def test_concurrent_flush_and_record_are_safe(self, tmp_path: Path) -> None:
        output = tmp_path / "logs" / "concurrent.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        client = TelemetryClient(enabled=True, output_path=output)
        errors: list[Exception] = []

        def _record() -> None:
            try:
                for _ in range(10):
                    client.record_event(_base_event())
            except Exception as exc:
                errors.append(exc)

        def _flush() -> None:
            try:
                client.flush()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_record if i % 2 == 0 else _flush) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"


# ===========================================================================
# TelemetryClient — error handling
# ===========================================================================


class TestTelemetryClientErrorHandling:
    def test_flush_handles_write_error_gracefully(self, tmp_path: Path) -> None:
        output = tmp_path / "telemetry.jsonl"
        mock_writer = MagicMock()
        mock_writer.append_jsonl.side_effect = OSError("disk full")

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        client.record_event(_base_event())
        # Should not raise
        result = client.flush()
        # Written count is 0 because the write failed
        assert result == 0
        # FR03: Failed events are restored to the queue for retry
        assert client.queue_size() == 1

    def test_flush_continues_after_partial_write_error(self, tmp_path: Path) -> None:
        output = tmp_path / "logs" / "partial.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        mock_writer = MagicMock()
        # First call raises, second succeeds
        call_count = {"n": 0}

        def _side_effect(path: Path, record: dict[str, object]) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("transient error")

        mock_writer.append_jsonl.side_effect = _side_effect

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        client.record_event(_base_event())
        client.record_event(_base_event())
        # Should not raise; one write succeeds
        result = client.flush()
        assert result == 1  # Only the second event succeeded
        assert mock_writer.append_jsonl.call_count == 2
        # FR03: The failed event is restored to the queue for retry
        assert client.queue_size() == 1


# ===========================================================================
# TelemetryClient.from_config factory
# ===========================================================================


class TestTelemetryClientFromConfig:
    def test_from_config_disabled_when_telemetry_enabled_false(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            telemetry_enabled=False,
        )
        # get_config is imported locally inside from_config(), so patch the source module
        with (
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.client.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
        ):
            client = TelemetryClient.from_config()

        assert client.enabled is False

    def test_from_config_enabled_when_telemetry_enabled_true(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            telemetry_enabled=True,
        )
        with (
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.client.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
        ):
            client = TelemetryClient.from_config()

        assert client.enabled is True

    def test_from_config_output_path_uses_logs_dir_and_telemetry_file(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            logs_dir="logs",
            telemetry_file="custom-telemetry.jsonl",
        )
        trw_dir = tmp_path / ".trw"
        with (
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.client.resolve_trw_dir",
                return_value=trw_dir,
            ),
        ):
            client = TelemetryClient.from_config()

        expected = trw_dir / "logs" / "custom-telemetry.jsonl"
        assert client._output_path == expected


# ===========================================================================
# FR03 — flush data loss prevention
# ===========================================================================


class TestFlushDataLossPrevention:
    """PRD-FIX-043 FR03: Failed events restored to queue on flush errors."""

    def test_all_events_restored_on_total_failure(self, tmp_path: Path) -> None:
        """When all writes fail, all events return to the queue."""
        output = tmp_path / "telemetry.jsonl"
        mock_writer = MagicMock()
        mock_writer.append_jsonl.side_effect = OSError("disk full")

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        for _ in range(3):
            client.record_event(_base_event())

        result = client.flush()
        assert result == 0
        assert client.queue_size() == 3

    def test_only_failed_events_restored(self, tmp_path: Path) -> None:
        """Mixed success: only failed events go back to queue."""
        output = tmp_path / "logs" / "mixed.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        mock_writer = MagicMock()
        call_idx = {"n": 0}

        def _side_effect(path: Path, record: dict[str, object]) -> None:
            call_idx["n"] += 1
            if call_idx["n"] == 2:
                raise OSError("transient")

        mock_writer.append_jsonl.side_effect = _side_effect

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        for _ in range(3):
            client.record_event(_base_event())

        result = client.flush()
        assert result == 2  # events 1 and 3 succeeded
        assert client.queue_size() == 1  # event 2 failed, restored

    def test_restored_events_can_be_retried(self, tmp_path: Path) -> None:
        """Failed events restored to queue succeed on second flush."""
        output = tmp_path / "logs" / "retry.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        mock_writer = MagicMock()
        call_idx = {"n": 0}

        def _side_effect(path: Path, record: dict[str, object]) -> None:
            call_idx["n"] += 1
            if call_idx["n"] == 1:
                raise OSError("transient")

        mock_writer.append_jsonl.side_effect = _side_effect

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        client.record_event(_base_event())

        # First flush fails
        result1 = client.flush()
        assert result1 == 0
        assert client.queue_size() == 1

        # Second flush succeeds (side_effect only fails call #1)
        result2 = client.flush()
        assert result2 == 1
        assert client.queue_size() == 0
