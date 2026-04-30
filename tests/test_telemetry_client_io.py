"""Tests for enabled and disabled TelemetryClient behavior."""

from __future__ import annotations

import json
from pathlib import Path

from tests._telemetry_client_support import _INSTALL_ID, _FW_VERSION, _base_event
from trw_mcp.telemetry.client import TelemetryClient
from trw_mcp.telemetry.models import SessionEndEvent, SessionStartEvent, ToolInvocationEvent


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
        lines = [line for line in output.read_text().splitlines() if line.strip()]
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
        lines = [line for line in output.read_text().splitlines() if line.strip()]
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
