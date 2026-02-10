"""Cost estimation and budget management for gate evaluation.

PRD-QUAL-005-FR10: Token cost estimation, budget checking, and model tier
selection for cost-optimized gate evaluation.

All cost constants are sourced from TRWConfig — no magic numbers.
"""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.gate import GateType, ModelTier

_config = TRWConfig()


def estimate_vote_cost(n: int, shard_size: int = 1000) -> int:
    """Estimate token cost for a vote-based evaluation.

    Args:
        n: Number of judges.
        shard_size: Approximate size of shard output in characters.

    Returns:
        Estimated total tokens.
    """
    base = _config.gate_tokens_per_vote * n
    shard_overhead = int((shard_size / 1000) * _config.gate_tokens_per_1k_chars) * n
    return base + shard_overhead


def estimate_debate_cost(n: int, rounds: int, shard_size: int = 1000) -> int:
    """Estimate token cost for a debate-based evaluation.

    Each round after the first includes prior round context.

    Args:
        n: Number of judges per round.
        rounds: Number of debate rounds.
        shard_size: Approximate size of shard output in characters.

    Returns:
        Estimated total tokens.
    """
    base_round = estimate_vote_cost(n, shard_size)
    multiplier = _config.gate_debate_context_multiplier
    total = base_round  # First round
    for r in range(1, rounds):
        round_cost = int(base_round * (1 + r * (multiplier - 1) / rounds))
        total += round_cost
    return total


def estimate_critic_cost(debate_cost: int) -> int:
    """Estimate additional cost for critic layer on top of debate.

    Args:
        debate_cost: Estimated debate cost.

    Returns:
        Total cost including critic overhead.
    """
    return int(debate_cost * _config.gate_critic_overhead_multiplier)


def check_budget(
    estimated: int,
    max_tokens: int,
) -> tuple[bool, str]:
    """Check if estimated cost fits within budget.

    Args:
        estimated: Estimated token cost.
        max_tokens: Maximum allowed tokens.

    Returns:
        Tuple of (within_budget, message).
    """
    if max_tokens <= 0:
        return True, "No budget limit set"

    if estimated <= max_tokens:
        return True, f"Within budget ({estimated}/{max_tokens} tokens)"

    return False, (
        f"Budget exceeded: estimated {estimated} tokens, "
        f"max {max_tokens} tokens"
    )


def select_model_tier(gate_type: str) -> ModelTier:
    """Select the default model tier for a gate type.

    LIGHT -> haiku, FULL -> sonnet, CRITIC -> opus.

    Args:
        gate_type: Gate type string.

    Returns:
        ModelTier for the gate type.
    """
    tier_map: dict[str, ModelTier] = {
        GateType.LIGHT.value: ModelTier.HAIKU,
        GateType.FULL.value: ModelTier.SONNET,
        GateType.CRITIC.value: ModelTier.OPUS,
    }
    return tier_map.get(gate_type, ModelTier.SONNET)
