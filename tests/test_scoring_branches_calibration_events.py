"""Branch tests for calibration and event reward resolution."""

from __future__ import annotations

import pytest

import trw_mcp.scoring as scoring_mod
from trw_mcp.models.run import EventType
from trw_mcp.scoring import REWARD_MAP


class TestBayesianCalibrate:
    """Tests for bayesian_calibrate — Bayesian posterior impact score."""

    def test_zero_weights_returns_user_impact(self) -> None:
        """Both weights=0 returns user_impact as fallback."""
        result = scoring_mod.bayesian_calibrate(0.7, user_weight=0.0, org_weight=0.0)
        assert result == pytest.approx(0.7)

    def test_equal_weights(self) -> None:
        """Equal weights average user and org mean."""
        result = scoring_mod.bayesian_calibrate(0.8, org_mean=0.4, user_weight=1.0, org_weight=1.0)
        assert result == pytest.approx(0.6)

    def test_user_heavy_weighting(self) -> None:
        """High user_weight keeps result close to user_impact."""
        result = scoring_mod.bayesian_calibrate(0.9, org_mean=0.3, user_weight=10.0, org_weight=1.0)
        assert result > 0.8

    def test_org_heavy_weighting(self) -> None:
        """High org_weight pulls result toward org_mean."""
        result = scoring_mod.bayesian_calibrate(0.9, org_mean=0.3, user_weight=0.5, org_weight=10.0)
        assert result < 0.5

    def test_org_weight_capped_at_two(self) -> None:
        """org_weight is capped at 2.0 internally."""
        result_high = scoring_mod.bayesian_calibrate(0.8, org_mean=0.2, user_weight=1.0, org_weight=100.0)
        result_capped = scoring_mod.bayesian_calibrate(0.8, org_mean=0.2, user_weight=1.0, org_weight=2.0)
        assert result_high == pytest.approx(result_capped)

    def test_result_clamped_to_unit_range(self) -> None:
        """Result is always in [0.0, 1.0]."""
        result = scoring_mod.bayesian_calibrate(1.0, org_mean=1.0, user_weight=1.0, org_weight=1.0)
        assert 0.0 <= result <= 1.0

    def test_default_org_mean(self) -> None:
        """Default org_mean is 0.5 (regression toward center)."""
        result = scoring_mod.bayesian_calibrate(0.8, user_weight=1.0, org_weight=0.5)
        assert result == pytest.approx((0.8 * 1.0 + 0.5 * 0.5) / 1.5, abs=0.01)

    @pytest.mark.parametrize("impact", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_various_user_impacts(self, impact: float) -> None:
        """All user impact values produce valid output in [0, 1]."""
        result = scoring_mod.bayesian_calibrate(impact)
        assert 0.0 <= result <= 1.0


class TestComputeCalibrationAccuracy:
    """Tests for compute_calibration_accuracy — weight based on recall history."""

    def test_no_recalls_returns_default(self) -> None:
        """Zero total recalls returns default weight of 1.0."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 0, "positive_outcomes": 0})
        assert result == pytest.approx(1.0)

    def test_empty_dict_returns_default(self) -> None:
        """Missing keys treated as 0, returns 1.0 default."""
        result = scoring_mod.compute_calibration_accuracy({})
        assert result == pytest.approx(1.0)

    def test_all_positive_returns_high_weight(self) -> None:
        """100% positive → weight 2.0."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 10, "positive_outcomes": 10})
        assert result == pytest.approx(2.0)

    def test_seventy_five_percent_positive(self) -> None:
        """75% positive → weight 2.0."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 8, "positive_outcomes": 6})
        assert result == pytest.approx(2.0)

    def test_fifty_percent_positive(self) -> None:
        """50% positive → weight 1.5."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 10, "positive_outcomes": 5})
        assert result == pytest.approx(1.5)

    def test_twenty_five_percent_positive(self) -> None:
        """25% positive → weight 1.0."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 8, "positive_outcomes": 2})
        assert result == pytest.approx(1.0)

    def test_below_twenty_five_percent(self) -> None:
        """<25% positive → weight 0.5."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 10, "positive_outcomes": 1})
        assert result == pytest.approx(0.5)

    def test_zero_positive_outcomes(self) -> None:
        """0 positive outcomes → weight 0.5 (below 25% threshold)."""
        result = scoring_mod.compute_calibration_accuracy({"total_recalls": 5, "positive_outcomes": 0})
        assert result == pytest.approx(0.5)


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
