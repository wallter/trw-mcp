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

import pytest

from trw_mcp.state.persistence import (
    INTERNAL_EVENT_TYPES,
    FileEventLogger,
    FileStateWriter,
    _suppress_internal_events,
    suppress_internal_events,
)


class TestSuppressInternalEventsFlag:
    """FR06: contextvars flag controls suppression in FileEventLogger.log_event()."""

    def test_internal_event_suppressed_when_flag_set(self, tmp_path: Path) -> None:
        """Internal event types are not written when suppression flag is active."""
        events_path = tmp_path / "events.jsonl"
        mock_writer = MagicMock(spec=FileStateWriter)
        logger = FileEventLogger(writer=mock_writer)

        with suppress_internal_events():
            for internal_type in INTERNAL_EVENT_TYPES:
                logger.log_event(events_path, internal_type, {"detail": "test"})

        # No append_jsonl calls for any internal event type
        mock_writer.append_jsonl.assert_not_called()

    def test_user_facing_events_not_suppressed_when_flag_set(self, tmp_path: Path) -> None:
        """User-facing events are written even when suppression flag is active."""
        events_path = tmp_path / "events.jsonl"
        mock_writer = MagicMock(spec=FileStateWriter)
        logger = FileEventLogger(writer=mock_writer)

        user_facing_types = [
            "tool_invocation",
            "session_start",
            "checkpoint",
            "build_check_complete",
            "phase_enter",
            "run_init",
            "review_complete",
        ]

        with suppress_internal_events():
            for event_type in user_facing_types:
                logger.log_event(events_path, event_type, {"tool": "trw_learn"})

        # All user-facing events must be written (one call per event type)
        assert mock_writer.append_jsonl.call_count == len(user_facing_types)

    def test_flag_resets_after_context_exit(self, tmp_path: Path) -> None:
        """After suppress_internal_events() block exits, flag is reset to False."""
        # Flag should start as False
        assert _suppress_internal_events.get() is False

        with suppress_internal_events():
            assert _suppress_internal_events.get() is True

        # Must be reset after context exits
        assert _suppress_internal_events.get() is False

    def test_flag_resets_on_exception(self, tmp_path: Path) -> None:
        """Flag is reset even when an exception occurs inside the context."""
        assert _suppress_internal_events.get() is False

        with pytest.raises(ValueError, match="test error"):
            with suppress_internal_events():
                assert _suppress_internal_events.get() is True
                raise ValueError("test error")

        assert _suppress_internal_events.get() is False

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

    def test_nested_suppression_contexts_safe(self, tmp_path: Path) -> None:
        """Nested suppress_internal_events contexts restore correctly."""
        assert _suppress_internal_events.get() is False

        with suppress_internal_events():
            assert _suppress_internal_events.get() is True
            with suppress_internal_events():
                assert _suppress_internal_events.get() is True
            # Inner context restored outer's True state
            assert _suppress_internal_events.get() is True

        # Outer context restored original False state
        assert _suppress_internal_events.get() is False

    def test_internal_event_types_set_contents(self) -> None:
        """INTERNAL_EVENT_TYPES contains the expected internal event names."""
        assert "jsonl_appended" in INTERNAL_EVENT_TYPES
        assert "yaml_written" in INTERNAL_EVENT_TYPES
        assert "vector_upserted" in INTERNAL_EVENT_TYPES
        # User-facing events must NOT be in this set
        assert "tool_invocation" not in INTERNAL_EVENT_TYPES
        assert "session_start" not in INTERNAL_EVENT_TYPES
        assert "checkpoint" not in INTERNAL_EVENT_TYPES

    def test_mixed_events_only_internal_suppressed(self, tmp_path: Path) -> None:
        """When flag is set, only internal types are suppressed; others pass through."""
        events_path = tmp_path / "events.jsonl"
        mock_writer = MagicMock(spec=FileStateWriter)
        logger = FileEventLogger(writer=mock_writer)

        with suppress_internal_events():
            logger.log_event(events_path, "yaml_written", {})  # suppressed
            logger.log_event(events_path, "tool_invocation", {"tool": "trw_learn"})  # not suppressed
            logger.log_event(events_path, "jsonl_appended", {})  # suppressed
            logger.log_event(events_path, "checkpoint", {"message": "mid-task"})  # not suppressed

        # Only 2 calls: tool_invocation and checkpoint
        assert mock_writer.append_jsonl.call_count == 2
        written_event_types = [call_args[0][1].get("event") for call_args in mock_writer.append_jsonl.call_args_list]
        assert "tool_invocation" in written_event_types
        assert "checkpoint" in written_event_types
