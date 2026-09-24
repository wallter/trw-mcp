"""Branch tests for event reward resolution."""

from __future__ import annotations

import pytest

import trw_mcp.scoring as scoring_mod
from trw_mcp.models.run import EventType
from trw_mcp.scoring import REWARD_MAP


class TestResolveEventReward:
    """Tests for _resolve_event_reward — event -> reward resolution."""

    def test_direct_reward_map_hit(self) -> None:
        """Direct REWARD_MAP match returns reward and event_type."""
        reward, label = scoring_mod._resolve_event_reward(EventType.TESTS_PASSED)
        assert reward == REWARD_MAP[EventType.TESTS_PASSED]
        assert label == EventType.TESTS_PASSED

    def test_phase_gate_passed(self) -> None:
        """phase_gate_passed maps to 1.0 reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.PHASE_GATE_PASSED)
        assert reward == pytest.approx(1.0)

    def test_phase_gate_failed(self) -> None:
        """phase_gate_failed maps to negative reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.PHASE_GATE_FAILED)
        assert reward is not None
        assert reward < 0

    def test_shard_complete_alias(self) -> None:
        """shard_completed alias resolves to shard_complete reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.SHARD_COMPLETED)
        assert reward is not None
        assert reward == REWARD_MAP[EventType.SHARD_COMPLETE]

    def test_shard_started_no_reward(self) -> None:
        """shard_started has explicit None alias (no reward)."""
        reward, label = scoring_mod._resolve_event_reward(EventType.SHARD_STARTED)
        assert reward is None
        assert label == EventType.SHARD_STARTED

    def test_run_init_no_reward(self) -> None:
        """run_init has None alias."""
        reward, label = scoring_mod._resolve_event_reward(EventType.RUN_INIT)
        assert reward is None

    def test_build_passed_alias(self) -> None:
        """build_passed alias resolves to float reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.BUILD_PASSED)
        assert reward is not None
        assert isinstance(reward, float)
        assert reward > 0

    def test_build_failed_alias(self) -> None:
        """build_failed alias resolves to negative float reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.BUILD_FAILED)
        assert reward is not None
        assert reward < 0

    def test_test_run_passed_data_aware(self) -> None:
        """test_run with passed=True routes to tests_passed reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.TEST_RUN, event_data={"passed": True})
        assert reward == REWARD_MAP[EventType.TESTS_PASSED]
        assert label == EventType.TESTS_PASSED

    def test_test_run_failed_data_aware(self) -> None:
        """test_run with passed=False routes to tests_failed reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.TEST_RUN, event_data={"passed": False})
        assert reward == REWARD_MAP[EventType.TESTS_FAILED]
        assert label == EventType.TESTS_FAILED

    def test_test_run_passed_string_true(self) -> None:
        """test_run with passed='true' (string) also routes to tests_passed."""
        reward, label = scoring_mod._resolve_event_reward(EventType.TEST_RUN, event_data={"passed": "true"})
        assert label == EventType.TESTS_PASSED

    def test_prd_status_change_to_approved(self) -> None:
        """prd_status_change with new_status=approved routes to prd_approved reward."""
        reward, label = scoring_mod._resolve_event_reward(
            EventType.PRD_STATUS_CHANGE, event_data={"new_status": "approved"}
        )
        assert reward == REWARD_MAP[EventType.PRD_APPROVED]
        assert label == EventType.PRD_APPROVED

    def test_prd_status_change_non_approved(self) -> None:
        """prd_status_change with other status gets None reward."""
        reward, label = scoring_mod._resolve_event_reward(
            EventType.PRD_STATUS_CHANGE, event_data={"new_status": "review"}
        )
        assert reward is None

    def test_compliance_check_passing_score(self) -> None:
        """compliance_check with score >= 0.8 returns compliance_passed reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.COMPLIANCE_CHECK, event_data={"score": 0.9})
        assert reward == REWARD_MAP[EventType.COMPLIANCE_PASSED]

    def test_compliance_check_failing_score(self) -> None:
        """compliance_check with score < 0.8 returns None."""
        reward, label = scoring_mod._resolve_event_reward(EventType.COMPLIANCE_CHECK, event_data={"score": 0.5})
        assert reward is None

    def test_compliance_check_invalid_score(self) -> None:
        """compliance_check with invalid score (non-numeric) returns None."""
        reward, label = scoring_mod._resolve_event_reward(
            EventType.COMPLIANCE_CHECK, event_data={"score": "not-a-number"}
        )
        assert reward is None

    def test_error_keyword_fallback(self) -> None:
        """Unknown event with error keyword gets fallback reward."""
        reward, label = scoring_mod._resolve_event_reward("some_error_event")
        assert reward is not None
        assert reward < 0

    def test_unknown_event_no_reward(self) -> None:
        """Completely unknown event with no keywords returns None label."""
        reward, label = scoring_mod._resolve_event_reward("totally_unknown_xyz")
        assert label == "totally_unknown_xyz"

    def test_checkpoint_alias(self) -> None:
        """checkpoint alias resolves to small positive reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.CHECKPOINT)
        assert reward is not None
        assert reward > 0

    def test_reflection_completed_alias(self) -> None:
        """reflection_completed alias resolves to reflection_complete reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.REFLECTION_COMPLETED)
        assert reward is not None
        assert reward == REWARD_MAP[EventType.REFLECTION_COMPLETE]

    def test_no_event_data_for_test_run(self) -> None:
        """test_run without event_data skips data-aware routing."""
        reward, label = scoring_mod._resolve_event_reward(EventType.TEST_RUN, event_data=None)
        assert reward is None

    def test_phase_revert_alias(self) -> None:
        """phase_revert alias resolves to negative float reward."""
        reward, label = scoring_mod._resolve_event_reward(EventType.PHASE_REVERT)
        assert reward is not None
        assert isinstance(reward, float)
        assert reward < 0
