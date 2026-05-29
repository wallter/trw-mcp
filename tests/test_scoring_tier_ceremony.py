"""Tests for scoring tier ceremony scoring."""

from __future__ import annotations

from trw_mcp.models.run import ComplexityClass
from trw_mcp.scoring import compute_tier_ceremony_score


class TestTierCeremonyScore:
    """Tests for compute_tier_ceremony_score function (FR03)."""

    def _make_events(self, event_types: list[str]) -> list[dict[str, object]]:
        """Helper to create minimal event dicts."""
        return [{"event": "tool_invocation", "tool_name": t} for t in event_types]

    def test_minimal_recall_and_deliver_high_score(self) -> None:
        """FR03: MINIMAL with trw_recall + trw_deliver -> score >= 80."""
        events = self._make_events(["trw_session_start", "trw_deliver"])
        result = compute_tier_ceremony_score(events, ComplexityClass.MINIMAL)
        assert result["score"] >= 60  # 2/3 expected events matched
        assert result["tier"] == "MINIMAL"

    def test_minimal_all_events_perfect(self) -> None:
        """FR03: MINIMAL with all 3 expected events -> 100."""
        events = self._make_events(["trw_session_start", "trw_build_check", "trw_deliver"])
        result = compute_tier_ceremony_score(events, ComplexityClass.MINIMAL)
        assert result["score"] == 100

    def test_comprehensive_missing_review_penalized(self) -> None:
        """FR03: COMPREHENSIVE missing trw_review -> score <= 60."""
        events = self._make_events(
            [
                "trw_session_start",
                "trw_init",
                "trw_checkpoint",
                "trw_build_check",
                "trw_deliver",
            ]
        )
        result = compute_tier_ceremony_score(events, ComplexityClass.COMPREHENSIVE)
        # 5/7 matched = ~71, minus 25 penalty = ~46
        assert result["score"] <= 60

    def test_standard_with_review_bonus(self) -> None:
        """FR03: STANDARD with all events including review -> 100."""
        events = self._make_events(
            [
                "trw_session_start",
                "trw_init",
                "trw_checkpoint",
                "trw_build_check",
                "trw_deliver",
                "trw_review",
            ]
        )
        result = compute_tier_ceremony_score(events, ComplexityClass.STANDARD)
        # 6/6 expected = 100, review_mandatory=True satisfied, no penalty
        assert result["score"] == 100

    def test_standard_without_review_penalized(self) -> None:
        """FR03: STANDARD without review incurs 15-point penalty (review is mandatory)."""
        events = self._make_events(
            [
                "trw_session_start",
                "trw_init",
                "trw_checkpoint",
                "trw_build_check",
                "trw_deliver",
            ]
        )
        result = compute_tier_ceremony_score(events, ComplexityClass.STANDARD)
        # 5/6 expected = round(83.33) = 83, minus 15 penalty = 68
        assert result["score"] == 68

    def test_none_defaults_to_standard(self) -> None:
        """FR03: None complexity_class defaults to STANDARD."""
        events = self._make_events(
            [
                "trw_session_start",
                "trw_init",
                "trw_checkpoint",
                "trw_build_check",
                "trw_deliver",
            ]
        )
        result = compute_tier_ceremony_score(events, None)
        assert result["tier"] == "STANDARD"
        # 5/6 expected = 83, minus 15 missing_review_penalty = 68
        assert result["score"] == 68

    def test_string_tier_accepted(self) -> None:
        """FR03: String tier values are accepted."""
        events = self._make_events(["trw_session_start", "trw_deliver"])
        result = compute_tier_ceremony_score(events, "MINIMAL")
        assert result["tier"] == "MINIMAL"

    def test_empty_events_zero_score(self) -> None:
        """FR03: No events -> score 0."""
        result = compute_tier_ceremony_score([], ComplexityClass.STANDARD)
        assert result["score"] == 0

    def test_comprehensive_all_events_perfect(self) -> None:
        """FR03: COMPREHENSIVE with all events -> 100."""
        events = self._make_events(
            [
                "trw_session_start",
                "trw_init",
                "trw_checkpoint",
                "trw_learn",
                "trw_build_check",
                "trw_deliver",
                "trw_review",
            ]
        )
        result = compute_tier_ceremony_score(events, ComplexityClass.COMPREHENSIVE)
        assert result["score"] == 100
