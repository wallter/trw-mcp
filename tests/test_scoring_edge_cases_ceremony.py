"""Edge-case tests for tier ceremony scoring with raw event variants."""

from __future__ import annotations

from trw_mcp.scoring import compute_tier_ceremony_score


class TestComputeTierCeremonyScoreRawEvents:
    """Test compute_tier_ceremony_score with raw event type strings (not tool_invocation)."""

    def test_session_start_raw_event(self) -> None:
        """Raw 'session_start' event counts as has_recall."""
        events: list[dict[str, object]] = [{"event": "session_start"}]
        result = compute_tier_ceremony_score(events, "MINIMAL")
        assert result["has_recall"] is True

    def test_run_init_raw_event(self) -> None:
        """Raw 'run_init' event counts as has_init."""
        events: list[dict[str, object]] = [{"event": "run_init"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_init"] is True

    def test_checkpoint_raw_event(self) -> None:
        """Raw 'checkpoint' event counts toward checkpoint_count."""
        events: list[dict[str, object]] = [
            {"event": "checkpoint"},
            {"event": "checkpoint"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["checkpoint_count"] == 2

    def test_build_check_complete_raw_event(self) -> None:
        """Raw 'build_check_complete' event counts as has_build_check."""
        events: list[dict[str, object]] = [{"event": "build_check_complete"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_build_check"] is True

    def test_learn_event_type_detected(self) -> None:
        """Event with 'learn' in event_type is detected as has_learn."""
        events: list[dict[str, object]] = [{"event": "learn_recorded"}]
        result = compute_tier_ceremony_score(events, "MINIMAL")
        assert result["has_learn"] is True

    def test_reflection_complete_raw_event(self) -> None:
        """Raw 'reflection_complete' event counts as has_deliver."""
        events: list[dict[str, object]] = [{"event": "reflection_complete"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_trw_deliver_complete_raw_event(self) -> None:
        """Raw 'trw_deliver_complete' event counts as has_deliver."""
        events: list[dict[str, object]] = [{"event": "trw_deliver_complete"}]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_review_complete_raw_event(self) -> None:
        """Raw 'review_complete' event counts as has_review."""
        events: list[dict[str, object]] = [{"event": "review_complete"}]
        result = compute_tier_ceremony_score(events, "COMPREHENSIVE")
        assert result["has_review"] is True

    def test_unknown_tier_string_defaults_to_standard(self) -> None:
        """Unknown tier string falls back to STANDARD."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
        ]
        result = compute_tier_ceremony_score(events, "NONEXISTENT_TIER")
        assert result["tier"] == "STANDARD"

    def test_lowercase_tier_string_normalized(self) -> None:
        """Lowercase tier string is normalized to uppercase."""
        events: list[dict[str, object]] = []
        result = compute_tier_ceremony_score(events, "minimal")
        assert result["tier"] == "MINIMAL"

    def test_mixed_raw_and_tool_events(self) -> None:
        """Both raw events and tool_invocation events are detected together."""
        events: list[dict[str, object]] = [
            {"event": "session_start"},
            {"event": "tool_invocation", "tool_name": "trw_init"},
            {"event": "checkpoint"},
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_recall"] is True
        assert result["has_init"] is True
        assert result["checkpoint_count"] == 1
        assert result["has_build_check"] is True
        assert result["has_deliver"] is True
        assert result["score"] == 68

    def test_tool_invocation_trw_reflect_counts_as_deliver(self) -> None:
        """tool_name='trw_reflect' counts as has_deliver."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_reflect"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["has_deliver"] is True

    def test_event_with_no_type_ignored(self) -> None:
        """Event dicts with no 'event' key are gracefully ignored."""
        events: list[dict[str, object]] = [
            {"tool_name": "trw_session_start"},
        ]
        result = compute_tier_ceremony_score(events, "STANDARD")
        assert result["matched_events"] == 0
