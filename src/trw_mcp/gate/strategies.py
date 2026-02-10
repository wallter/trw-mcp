"""Gate evaluation strategies — vote, debate, hybrid, critic.

PRD-QUAL-005-FR01-FR05: Strategy implementations with dependency-injected
judge functions for testability. All strategies accept a judge_fn callable
that returns JudgeVote objects (mock in tests, LLM in production).
"""

from __future__ import annotations

from typing import Callable, Protocol

from trw_mcp.gate.cost_model import (
    check_budget,
    estimate_debate_cost,
    estimate_vote_cost,
)
from trw_mcp.gate.early_stopping import evaluate_early_stop
from trw_mcp.models.gate import (
    EvaluationOutcome,
    EvaluationResult,
    GateConfig,
    JudgeVote,
)

JudgeFn = Callable[[str, str, int], JudgeVote]
"""judge_fn(judge_id, shard_output, round_number) -> JudgeVote."""


class GateStrategyProtocol(Protocol):
    """Protocol for gate evaluation strategies."""

    def evaluate(
        self,
        shard_output: str,
        config: GateConfig,
        judge_fn: JudgeFn,
    ) -> EvaluationResult: ...


def _compute_agreement(votes: list[JudgeVote], threshold: float = 0.7) -> float:
    """Compute agreement ratio among votes.

    Args:
        votes: List of judge votes.
        threshold: Score threshold for pass/fail.

    Returns:
        Fraction of votes on the majority side.
    """
    if not votes:
        return 0.0
    above = sum(1 for v in votes if v.score >= threshold)
    below = len(votes) - above
    majority = max(above, below)
    return majority / len(votes)


def _aggregate_result(
    votes: list[JudgeVote],
    rounds_used: int,
    config: GateConfig,
    token_cost: int = 0,
) -> EvaluationResult:
    """Aggregate votes into a final evaluation result.

    Args:
        votes: All collected votes across rounds.
        rounds_used: Number of rounds executed.
        config: Gate configuration.
        token_cost: Estimated token cost.

    Returns:
        Final EvaluationResult.
    """
    if not votes:
        return EvaluationResult(
            result="fallback",
            confidence=config.fallback.confidence_floor,
            rounds_used=rounds_used,
            reasoning="No votes collected",
        )

    scores = [v.score for v in votes]
    avg_score = sum(scores) / len(scores)
    avg_confidence = sum(v.confidence for v in votes) / len(votes)
    agreement = _compute_agreement(votes, config.score_threshold)

    # Determine pass/fail based on quorum
    above = sum(1 for v in votes if v.score >= config.score_threshold)
    pass_ratio = above / len(votes)

    result: EvaluationOutcome
    if pass_ratio >= config.quorum_threshold:
        result = "pass"
    elif pass_ratio <= (1 - config.quorum_threshold):
        result = "fail"
    else:
        result = "escalate"

    return EvaluationResult(
        result=result,
        confidence=round(avg_confidence, 4),
        agreement_ratio=round(agreement, 4),
        rounds_used=rounds_used,
        judges_used=len(votes),
        token_cost=token_cost,
        reasoning=f"Avg score: {avg_score:.3f}, pass ratio: {pass_ratio:.3f}",
        individual_scores=scores,
    )


class VoteStrategy:
    """Single-round parallel vote evaluation.

    All judges vote independently in a single round.
    Anti-sycophancy: judges don't see each other's votes.
    """

    def evaluate(
        self,
        shard_output: str,
        config: GateConfig,
        judge_fn: JudgeFn,
    ) -> EvaluationResult:
        """Execute single-round vote evaluation."""
        cost = estimate_vote_cost(config.quorum_size)
        budget_ok, budget_msg = check_budget(cost, config.cost.max_total_tokens)
        if not budget_ok:
            return EvaluationResult(
                result="fallback",
                confidence=config.fallback.confidence_floor,
                reasoning=budget_msg,
            )

        votes = [
            judge_fn(f"judge-{i}", shard_output, 1)
            for i in range(config.quorum_size)
        ]

        return _aggregate_result(votes, 1, config, cost)


class DebateStrategy:
    """Multi-round debate evaluation with anti-conformity prompting.

    Judges debate across rounds, with position tracking to prevent
    conformity bias. Early stopping when consensus is reached.
    """

    def evaluate(
        self,
        shard_output: str,
        config: GateConfig,
        judge_fn: JudgeFn,
    ) -> EvaluationResult:
        """Execute multi-round debate evaluation.

        Args:
            shard_output: The shard output to evaluate.
            config: Gate configuration.
            judge_fn: Judge function to call for each vote.

        Returns:
            EvaluationResult.
        """
        cost = estimate_debate_cost(config.quorum_size, config.max_rounds)
        budget_ok, budget_msg = check_budget(cost, config.cost.max_total_tokens)
        if not budget_ok:
            return EvaluationResult(
                result="fallback",
                confidence=config.fallback.confidence_floor,
                reasoning=budget_msg,
            )

        all_votes: list[JudgeVote] = []
        prev_round_votes: list[JudgeVote] | None = None

        for round_num in range(1, config.max_rounds + 1):
            round_votes: list[JudgeVote] = []
            for i in range(config.quorum_size):
                vote = judge_fn(f"judge-{i}", shard_output, round_num)
                round_votes.append(vote)
            all_votes.extend(round_votes)

            # Check early stopping
            should_stop, _ = evaluate_early_stop(
                round_votes, prev_round_votes, config,
            )
            if should_stop:
                return _aggregate_result(all_votes, round_num, config, cost)

            prev_round_votes = round_votes

        return _aggregate_result(all_votes, config.max_rounds, config, cost)


class HybridStrategy:
    """Vote-first, debate-on-split strategy.

    Starts with a single vote round. If consensus is reached, returns.
    If split, escalates to debate rounds.
    """

    def evaluate(
        self,
        shard_output: str,
        config: GateConfig,
        judge_fn: JudgeFn,
    ) -> EvaluationResult:
        """Execute hybrid vote-then-debate evaluation.

        Args:
            shard_output: The shard output to evaluate.
            config: Gate configuration.
            judge_fn: Judge function to call for each vote.

        Returns:
            EvaluationResult.
        """
        # Phase 1: Vote
        vote_strategy = VoteStrategy()
        initial = vote_strategy.evaluate(shard_output, config, judge_fn)

        if initial.result in ("pass", "fail"):
            return initial

        # Phase 2: Debate on split
        debate_config = config.model_copy(update={"max_rounds": max(1, config.max_rounds - 1)})
        debate_strategy = DebateStrategy()
        debate_result = debate_strategy.evaluate(shard_output, debate_config, judge_fn)

        # Merge results
        total_judges = initial.judges_used + debate_result.judges_used
        total_cost = initial.token_cost + debate_result.token_cost
        total_rounds = 1 + debate_result.rounds_used

        return EvaluationResult(
            result=debate_result.result,
            confidence=debate_result.confidence,
            agreement_ratio=debate_result.agreement_ratio,
            rounds_used=total_rounds,
            judges_used=total_judges,
            token_cost=total_cost,
            reasoning=f"Hybrid: vote split, then debate. {debate_result.reasoning}",
            individual_scores=debate_result.individual_scores,
        )


class CriticStrategy:
    """Debate + critic + judge evaluation.

    Runs debate first, then adds a critic layer that reviews the debate
    transcript and provides a meta-evaluation.
    """

    def evaluate(
        self,
        shard_output: str,
        config: GateConfig,
        judge_fn: JudgeFn,
    ) -> EvaluationResult:
        """Execute critic-layer evaluation.

        Args:
            shard_output: The shard output to evaluate.
            config: Gate configuration.
            judge_fn: Judge function to call for each vote.

        Returns:
            EvaluationResult.
        """
        # Phase 1: Debate
        debate_strategy = DebateStrategy()
        debate_result = debate_strategy.evaluate(shard_output, config, judge_fn)

        # Phase 2: Critic review
        critic_vote = judge_fn("critic-0", shard_output, config.max_rounds + 1)

        # Phase 3: Final judge
        final_vote = judge_fn("final-judge", shard_output, config.max_rounds + 2)

        # Combine critic + final into result
        all_scores = debate_result.individual_scores + [critic_vote.score, final_vote.score]
        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

        total_judges = debate_result.judges_used + 2
        total_rounds = debate_result.rounds_used + 2

        above = sum(1 for s in all_scores if s >= config.score_threshold)
        pass_ratio = above / len(all_scores) if all_scores else 0.0

        result: EvaluationOutcome
        if pass_ratio >= config.quorum_threshold:
            result = "pass"
        else:
            result = "fail"

        return EvaluationResult(
            result=result,
            confidence=round(
                (debate_result.confidence + critic_vote.confidence + final_vote.confidence) / 3, 4,
            ),
            agreement_ratio=debate_result.agreement_ratio,
            rounds_used=total_rounds,
            judges_used=total_judges,
            token_cost=debate_result.token_cost,
            reasoning=f"Critic: debate + critic + final. Avg: {avg_score:.3f}",
            individual_scores=all_scores,
        )


def get_strategy(strategy_name: str) -> GateStrategyProtocol:
    """Get a strategy instance by name.

    Args:
        strategy_name: Strategy name (vote, debate, hybrid, critic).

    Returns:
        Strategy instance.

    Raises:
        ValueError: If strategy name is unknown.
    """
    strategies: dict[str, GateStrategyProtocol] = {
        "vote": VoteStrategy(),
        "debate": DebateStrategy(),
        "hybrid": HybridStrategy(),
        "critic": CriticStrategy(),
    }
    if strategy_name not in strategies:
        valid = list(strategies.keys())
        raise ValueError(f"Unknown strategy: {strategy_name!r}. Valid: {valid}")
    return strategies[strategy_name]
