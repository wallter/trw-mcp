"""Edge-case tests for scoring math and event reward resolution."""

from __future__ import annotations

from trw_mcp.models.run import EventType
from trw_mcp.scoring import compute_utility_score


class TestComputeUtilityScoreMath:
    """Mathematical correctness checks for compute_utility_score."""

    def test_access_count_boost(self) -> None:
        """access_count parameter provides a boost."""
        score_no_access = compute_utility_score(7, 1, 0.5, access_count=0)
        score_with_access = compute_utility_score(7, 1, 0.5, access_count=10)
        assert score_with_access >= score_no_access

    def test_source_human_boost(self) -> None:
        """source_type='human' provides a utility boost."""
        score_agent = compute_utility_score(7, 1, 0.5, source_type="agent")
        score_human = compute_utility_score(7, 1, 0.5, source_type="human")
        assert score_human >= score_agent

    def test_very_high_recurrence_caps_benefit(self) -> None:
        """Extremely high recurrence doesn't produce score > 1.0."""
        score = compute_utility_score(0, 10000, 1.0)
        assert score <= 1.0

    def test_zero_half_life_handled(self) -> None:
        """half_life_days=0 doesn't cause division by zero."""
        score = compute_utility_score(10, 1, 0.5, half_life_days=0.0)
        assert 0.0 <= score <= 1.0

    def test_access_count_boost_capped(self) -> None:
        """Access count boost has a cap (doesn't grow indefinitely)."""
        score_100 = compute_utility_score(7, 1, 0.5, access_count=100)
        score_10000 = compute_utility_score(7, 1, 0.5, access_count=10000)
        assert abs(score_10000 - score_100) < 0.05


class TestResolveEventRewardAdditional:
    """Additional edge cases for _resolve_event_reward."""

    def test_prd_created_alias_positive(self) -> None:
        """PRD_CREATED alias returns positive reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.PRD_CREATED)
        assert reward is not None
        assert reward > 0

    def test_wave_validated_alias_resolves(self) -> None:
        """WAVE_VALIDATED resolves to WAVE_VALIDATION_PASSED reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.WAVE_VALIDATED)
        assert reward is not None
        assert reward == 0.7

    def test_wave_completed_alias_resolves(self) -> None:
        """WAVE_COMPLETED resolves to WAVE_COMPLETE reward."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.WAVE_COMPLETED)
        assert reward is not None
        assert reward == 0.8

    def test_phase_enter_no_reward(self) -> None:
        """PHASE_ENTER has None alias (no reward)."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.PHASE_ENTER)
        assert reward is None

    def test_session_start_no_reward(self) -> None:
        """SESSION_START has None alias (no reward)."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.SESSION_START)
        assert reward is None

    def test_run_resumed_no_reward(self) -> None:
        """RUN_RESUMED has None alias (no reward)."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(EventType.RUN_RESUMED)
        assert reward is None

    def test_compliance_check_no_score_key(self) -> None:
        """COMPLIANCE_CHECK without score key returns None via alias path."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(
            EventType.COMPLIANCE_CHECK,
            event_data={"other": "data"},
        )
        assert reward is None

    def test_test_run_with_empty_event_data(self) -> None:
        """TEST_RUN with empty dict event_data: {} is falsy, skips data-aware routing."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(
            EventType.TEST_RUN,
            event_data={},
        )
        assert reward is None
        assert label == EventType.TEST_RUN

    def test_error_keyword_in_mixed_case(self) -> None:
        """Error keyword matching is case-insensitive."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward("SOME_ERROR_HAPPENED")
        assert reward is not None
        assert reward < 0

    def test_prd_status_change_approved_uppercase(self) -> None:
        """PRD_STATUS_CHANGE with 'Approved' (capitalized) matches case-insensitively."""
        from trw_mcp.scoring import _resolve_event_reward

        reward, label = _resolve_event_reward(
            EventType.PRD_STATUS_CHANGE,
            event_data={"new_status": "Approved"},
        )
        assert reward is not None
        assert label == EventType.PRD_APPROVED
