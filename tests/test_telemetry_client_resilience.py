"""Tests for TelemetryClient concurrency and error recovery."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from tests._telemetry_client_support import _base_event
from trw_mcp.telemetry.client import TelemetryClient


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
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

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
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, f"Thread errors: {errors}"


class TestTelemetryClientErrorHandling:
    def test_flush_handles_write_error_gracefully(self, tmp_path: Path) -> None:
        output = tmp_path / "telemetry.jsonl"
        mock_writer = MagicMock()
        mock_writer.append_jsonl.side_effect = OSError("disk full")

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        client.record_event(_base_event())
        result = client.flush()
        assert result == 0
        assert client.queue_size() == 1

    def test_flush_continues_after_partial_write_error(self, tmp_path: Path) -> None:
        output = tmp_path / "logs" / "partial.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        mock_writer = MagicMock()
        call_count = {"n": 0}

        def _side_effect(path: Path, record: dict[str, object]) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("transient error")

        mock_writer.append_jsonl.side_effect = _side_effect

        client = TelemetryClient(enabled=True, output_path=output, writer=mock_writer)
        client.record_event(_base_event())
        client.record_event(_base_event())
        result = client.flush()
        assert result == 1
        assert mock_writer.append_jsonl.call_count == 2
        assert client.queue_size() == 1


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
        assert result == 2
        assert client.queue_size() == 1

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

        result1 = client.flush()
        assert result1 == 0
        assert client.queue_size() == 1

        result2 = client.flush()
        assert result2 == 1
        assert client.queue_size() == 0
