"""Tests for PRD-FIX-027 scoring event wiring."""

from __future__ import annotations

from trw_mcp.models.run import EventType


class TestDeliverCompleteEventType:
    """Bug 1: DELIVER_COMPLETE must exist in EventType."""

    def test_deliver_complete_exists_in_eventtype(self) -> None:
        """EventType.DELIVER_COMPLETE must be a member of the enum."""
        assert hasattr(EventType, "DELIVER_COMPLETE"), (
            "EventType.DELIVER_COMPLETE is missing — trw_deliver() events are silently dropped"
        )

    def test_deliver_complete_value(self) -> None:
        """DELIVER_COMPLETE must map to the string logged by trw_deliver()."""
        assert EventType.DELIVER_COMPLETE == "trw_deliver_complete"

    def test_deliver_complete_resolve_from_string(self) -> None:
        """EventType.resolve('trw_deliver_complete') must return the enum member."""
        resolved = EventType.resolve("trw_deliver_complete")
        assert resolved is EventType.DELIVER_COMPLETE
