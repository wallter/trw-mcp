"""Ceremony scoring tests for analytics report state helpers."""

from __future__ import annotations

import pytest

from trw_mcp.state.analytics.report import compute_ceremony_score


class TestCeremonyScoring:
    """T-10 through T-13: compute_ceremony_score pure function tests."""

    def test_empty_events_score_zero(self) -> None:
        """T-10: Empty event list produces score of 0."""
        result = compute_ceremony_score([])
        assert result["score"] == 0
        assert result["session_start"] is False
        assert result["deliver"] is False
        assert result["checkpoint_count"] == 0
        assert result["learn_count"] == 0
        assert result["build_check"] is False
        assert result["build_passed"] is None
        assert result["review"] is False

    def test_all_six_event_types_score_100(self) -> None:
        """T-11: All 6 event types (including review) present yields score of 100."""
        events: list[dict[str, object]] = [
            {"event": "session_start"},
            {"event": "reflection_complete"},
            {"event": "checkpoint"},
            {"event": "learn_recorded"},
            {"event": "build_check_complete", "tests_passed": "true"},
            {"event": "review_complete"},
        ]
        result = compute_ceremony_score(events)
        assert result["score"] == 100
        assert result["session_start"] is True
        assert result["deliver"] is True
        assert result["checkpoint_count"] == 1
        assert result["learn_count"] == 1
        assert result["build_check"] is True
        assert result["build_passed"] is True
        assert result["review"] is True

    def test_session_start_only_score_25(self) -> None:
        """T-12: Only session_start event yields score of 25."""
        events: list[dict[str, object]] = [{"event": "session_start"}]
        result = compute_ceremony_score(events)
        assert result["score"] == 25
        assert result["session_start"] is True
        assert result["deliver"] is False
        assert result["checkpoint_count"] == 0
        assert result["review"] is False

    @pytest.mark.parametrize(
        "event_types, expected_score",
        [
            ([], 0),
            (["session_start"], 25),
            (["reflection_complete"], 25),
            (["checkpoint"], 20),
            (["learn_recorded"], 10),
            (["build_check_complete"], 10),
            (["session_start", "reflection_complete"], 50),
            (["session_start", "checkpoint"], 45),
            (["session_start", "reflection_complete", "learn_saved"], 60),
            (["checkpoint", "learn_recorded", "build_check_complete"], 40),
            (["session_start", "checkpoint", "learn_recorded", "build_check_complete"], 65),
            (["session_start", "reflection_complete", "checkpoint", "learn_recorded"], 80),
            (
                [
                    "session_start",
                    "reflection_complete",
                    "checkpoint",
                    "learn_recorded",
                    "build_check_complete",
                ],
                90,
            ),
            (
                [
                    "session_start",
                    "reflection_complete",
                    "checkpoint",
                    "learn_recorded",
                    "build_check_complete",
                    "review_complete",
                ],
                100,
            ),
        ],
    )
    def test_additive_scoring_parametrized(
        self,
        event_types: list[str],
        expected_score: int,
    ) -> None:
        """T-13: Additive scoring — combinations of event types sum correctly."""
        events: list[dict[str, object]] = [{"event": t} for t in event_types]
        result = compute_ceremony_score(events)
        assert result["score"] == expected_score, (
            f"events={event_types!r} expected score={expected_score}, got {result['score']}"
        )

    def test_multiple_checkpoints_counted(self) -> None:
        """Multiple checkpoint events increment checkpoint_count; score still capped at 20 pts."""
        events: list[dict[str, object]] = [
            {"event": "checkpoint"},
            {"event": "checkpoint"},
            {"event": "checkpoint"},
        ]
        result = compute_ceremony_score(events)
        assert result["checkpoint_count"] == 3
        assert result["score"] == 20

    def test_multiple_learn_events_counted(self) -> None:
        """Multiple learn events increment learn_count; score component still capped at 10 pts."""
        events: list[dict[str, object]] = [
            {"event": "learn_recorded"},
            {"event": "learn_saved"},
            {"event": "new_learning"},
        ]
        result = compute_ceremony_score(events)
        assert result["learn_count"] == 3
        assert result["score"] == 10

    def test_build_passed_false_when_tests_passed_false(self) -> None:
        """build_passed is False when tests_passed field is 'false'."""
        events: list[dict[str, object]] = [
            {"event": "build_check_complete", "tests_passed": "false"},
        ]
        result = compute_ceremony_score(events)
        assert result["build_check"] is True
        assert result["build_passed"] is False

    def test_build_passed_none_without_build_event(self) -> None:
        """build_passed is None when no build_check_complete event is present."""
        events: list[dict[str, object]] = [{"event": "session_start"}]
        result = compute_ceremony_score(events)
        assert result["build_passed"] is None

    def test_unrecognized_events_ignored(self) -> None:
        """Unknown event types do not contribute to the score."""
        events: list[dict[str, object]] = [
            {"event": "run_init"},
            {"event": "phase_transition"},
            {"event": "tool_call"},
        ]
        result = compute_ceremony_score(events)
        assert result["score"] == 0
