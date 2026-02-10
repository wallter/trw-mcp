"""Tests for gate scaling — early stopping, escalation, cost model.

PRD-QUAL-005-FR06/FR07/FR10: Dynamic quorum scaling and cost management.
"""

from __future__ import annotations

import pytest

from trw_mcp.gate.cost_model import (
    check_budget,
    estimate_critic_cost,
    estimate_debate_cost,
    estimate_vote_cost,
    select_model_tier,
)
from trw_mcp.gate.early_stopping import (
    EarlyStopLevel,
    check_confidence_threshold,
    check_ks_stability,
    check_unanimous,
    evaluate_early_stop,
)
from trw_mcp.gate.escalation import (
    compute_escalation_judges,
    compute_vote_margin,
    ensure_odd,
    escalate,
)
from trw_mcp.models.gate import (
    EscalationConfig,
    GatePreset,
    JudgeVote,
)


def _vote(judge_id: str, score: float, confidence: float = 0.8) -> JudgeVote:
    """Create a JudgeVote with sensible defaults."""
    return JudgeVote(judge_id=judge_id, score=score, confidence=confidence)


def _votes_uniform(n: int, score: float, confidence: float = 0.8) -> list[JudgeVote]:
    """Create n votes with identical score and confidence."""
    return [_vote(f"j{i}", score, confidence) for i in range(n)]


class TestCheckUnanimous:
    def test_all_above_threshold(self) -> None:
        votes = [_vote("j1", 0.9, 0.95), _vote("j2", 0.85, 0.9), _vote("j3", 0.8, 0.88)]
        assert check_unanimous(votes) is True

    def test_all_below_threshold(self) -> None:
        votes = [_vote("j1", 0.3, 0.9), _vote("j2", 0.4, 0.85)]
        assert check_unanimous(votes) is True

    def test_disagreement(self) -> None:
        votes = [_vote("j1", 0.9, 0.9), _vote("j2", 0.4, 0.85)]
        assert check_unanimous(votes) is False

    def test_empty_votes(self) -> None:
        assert check_unanimous([]) is False


class TestCheckConfidenceThreshold:
    def test_high_confidence(self) -> None:
        votes = [_vote("j1", 0.8, 0.95), _vote("j2", 0.85, 0.90)]
        assert check_confidence_threshold(votes, 0.85) is True

    def test_low_confidence(self) -> None:
        votes = [_vote("j1", 0.8, 0.6), _vote("j2", 0.85, 0.7)]
        assert check_confidence_threshold(votes, 0.85) is False


class TestCheckKSStability:
    def test_stable(self) -> None:
        assert check_ks_stability([0.8, 0.82, 0.79], [0.81, 0.80, 0.78], 0.05) is True

    def test_unstable(self) -> None:
        assert check_ks_stability([0.8, 0.82, 0.79], [0.5, 0.55, 0.48], 0.05) is False

    def test_empty(self) -> None:
        assert check_ks_stability([], [0.5], 0.05) is False


class TestEvaluateEarlyStop:
    def test_unanimous_stops(self) -> None:
        votes = [_vote("j1", 0.9, 0.95), _vote("j2", 0.85, 0.9), _vote("j3", 0.8, 0.88)]
        should_stop, level = evaluate_early_stop(votes, None, GatePreset.full())
        assert should_stop is True
        assert level == EarlyStopLevel.UNANIMOUS

    def test_disagreement_continues(self) -> None:
        votes = [_vote("j1", 0.9, 0.5), _vote("j2", 0.3, 0.5), _vote("j3", 0.8, 0.5)]
        should_stop, level = evaluate_early_stop(votes, None, GatePreset.full())
        assert should_stop is False
        assert level is None


class TestComputeVoteMargin:
    def test_unanimous(self) -> None:
        votes = _votes_uniform(3, score=0.9)
        assert compute_vote_margin(votes) == 1.0

    def test_near_tie(self) -> None:
        votes = [_vote("j1", 0.9), _vote("j2", 0.3)]
        assert compute_vote_margin(votes) == pytest.approx(0.0, abs=0.01)

    def test_weak_majority(self) -> None:
        votes = [_vote("j1", 0.9), _vote("j2", 0.9), _vote("j3", 0.3)]
        margin = compute_vote_margin(votes)
        assert 0.2 < margin < 0.5


class TestEnsureOdd:
    def test_odd_unchanged(self) -> None:
        assert ensure_odd(5) == 5

    def test_even_incremented(self) -> None:
        assert ensure_odd(4) == 5

    def test_one(self) -> None:
        assert ensure_odd(1) == 1


class TestComputeEscalationJudges:
    """Uses a shared config with max_total_judges=13 for all cases."""

    config = EscalationConfig(max_total_judges=13)

    def test_near_tie_adds_four(self) -> None:
        additional = compute_escalation_judges(0.1, 3, self.config)
        assert additional >= self.config.near_tie_judges

    def test_weak_majority_adds_two(self) -> None:
        additional = compute_escalation_judges(0.3, 3, self.config)
        assert additional >= self.config.weak_majority_judges

    def test_strong_majority_no_escalation(self) -> None:
        additional = compute_escalation_judges(0.8, 3, self.config)
        assert additional == 0

    def test_cap_at_max(self) -> None:
        additional = compute_escalation_judges(0.1, 12, self.config)
        assert 12 + additional <= self.config.max_total_judges

    def test_already_at_max(self) -> None:
        additional = compute_escalation_judges(0.1, 13, self.config)
        assert additional == 0


class TestEscalate:
    def test_disabled(self) -> None:
        result = escalate([_vote("j1", 0.5)], EscalationConfig(enabled=False))
        assert result.should_escalate is False

    def test_max_reached(self) -> None:
        votes = _votes_uniform(3, score=0.5)
        result = escalate(votes, EscalationConfig(max_total_judges=3))
        assert result.should_escalate is False


class TestEstimateCosts:
    def test_vote_cost_scales_with_judges(self) -> None:
        assert estimate_vote_cost(5) > estimate_vote_cost(3)

    def test_debate_cost_more_than_vote(self) -> None:
        assert estimate_debate_cost(3, 3) > estimate_vote_cost(3)

    def test_critic_cost_overhead(self) -> None:
        debate_cost = estimate_debate_cost(5, 3)
        assert estimate_critic_cost(debate_cost) > debate_cost


class TestCheckBudget:
    def test_within_budget(self) -> None:
        ok, msg = check_budget(50_000, 100_000)
        assert ok is True
        assert "Within" in msg

    def test_exceeded(self) -> None:
        ok, msg = check_budget(150_000, 100_000)
        assert ok is False
        assert "exceeded" in msg

    def test_no_limit(self) -> None:
        ok, _ = check_budget(999_999, 0)
        assert ok is True


class TestSelectModelTier:
    def test_light_uses_haiku(self) -> None:
        assert select_model_tier("LIGHT").value == "haiku"

    def test_full_uses_sonnet(self) -> None:
        assert select_model_tier("FULL").value == "sonnet"

    def test_critic_uses_opus(self) -> None:
        assert select_model_tier("CRITIC").value == "opus"

    def test_unknown_defaults_sonnet(self) -> None:
        assert select_model_tier("UNKNOWN").value == "sonnet"
