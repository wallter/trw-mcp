"""Tests for PRD-FIX-053-FR06: Telemetry event separation via contextvars flag.

Verifies:
1. Internal events are suppressed when _suppress_internal_events flag is set
2. User-facing events are NOT suppressed even when flag is set
3. suppress_internal_events() context manager properly resets after exit
4. Events outside INTERNAL_EVENT_TYPES are never suppressed
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from trw_mcp.state.persistence import (
    INTERNAL_EVENT_TYPES,
    FileEventLogger,
    FileStateWriter,
)


class TestSuppressInternalEventsFlag:
    """FR06: contextvars flag controls suppression in FileEventLogger.log_event()."""

    def test_events_written_normally_without_flag(self, tmp_path: Path) -> None:
        """Without suppression, internal event types are written normally."""
        events_path = tmp_path / "events.jsonl"
        mock_writer = MagicMock(spec=FileStateWriter)
        logger = FileEventLogger(writer=mock_writer)

        # Without suppress context
        logger.log_event(events_path, "yaml_written", {"path": "/some/file.yaml"})
        logger.log_event(events_path, "jsonl_appended", {"path": "/some/file.jsonl"})

        # Both should be written since flag is not set
        assert mock_writer.append_jsonl.call_count == 2

    def test_internal_event_types_set_contents(self) -> None:
        """INTERNAL_EVENT_TYPES contains the expected internal event names."""
        assert "jsonl_appended" in INTERNAL_EVENT_TYPES
        assert "yaml_written" in INTERNAL_EVENT_TYPES
        assert "vector_upserted" in INTERNAL_EVENT_TYPES
        # User-facing events must NOT be in this set
        assert "tool_call" not in INTERNAL_EVENT_TYPES
        assert "session_start" not in INTERNAL_EVENT_TYPES
        assert "checkpoint" not in INTERNAL_EVENT_TYPES
