"""Adaptive gate models — strategies, presets, votes, evaluation results.

PRD-QUAL-005: Formal consensus math, dynamic quorum scaling, cost-optimized
tiering, and gate presets for phase gate evaluation.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Constrained string types for config fields with a fixed set of valid values.
TieStrategy = Literal["escalate", "fallback", "fail"]
BudgetAction = Literal["fallback", "fail", "warn"]
FallbackAction = Literal["pass_with_warning", "fail", "manual_review"]
EvaluationOutcome = Literal["pass", "fail", "escalate", "fallback"]


class GateStrategy(str, Enum):
    """Gate evaluation strategy."""

    VOTE = "vote"
    DEBATE = "debate"
    HYBRID = "hybrid"
    CRITIC = "critic"


class GateType(str, Enum):
    """Gate cost tier."""

    LIGHT = "LIGHT"
    FULL = "FULL"
    CRITIC = "CRITIC"


class ModelTier(str, Enum):
    """LLM model tier for cost estimation."""

    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class EscalationConfig(BaseModel):
    """Configuration for judge escalation on disagreement."""

    model_config = ConfigDict(use_enum_values=True)

    enabled: bool = True
    max_total_judges: int = Field(ge=1, le=21, default=13)
    max_escalation_rounds: int = Field(ge=0, le=5, default=3)
    tie_strategy: TieStrategy = "escalate"
    near_tie_margin: float = Field(ge=0.0, le=1.0, default=0.2)
    weak_majority_margin: float = Field(ge=0.0, le=1.0, default=0.4)
    near_tie_judges: int = Field(ge=1, le=10, default=4)
    weak_majority_judges: int = Field(ge=1, le=10, default=2)


class CostConfig(BaseModel):
    """Cost and budget configuration for gate evaluation."""

    model_config = ConfigDict(use_enum_values=True)

    model_tier: ModelTier = ModelTier.SONNET
    max_total_tokens: int = Field(ge=0, default=100_000)
    on_budget_exceeded: BudgetAction = "fallback"


class FallbackConfig(BaseModel):
    """Fallback behavior when gate evaluation cannot complete."""

    model_config = ConfigDict(use_enum_values=True)

    action: FallbackAction = "pass_with_warning"
    confidence_floor: float = Field(ge=0.0, le=1.0, default=0.5)


class RubricWeights(BaseModel):
    """Evaluation rubric weights — must sum to 1.0."""

    model_config = ConfigDict(use_enum_values=True)

    correctness: float = Field(ge=0.0, le=1.0, default=0.4)
    completeness: float = Field(ge=0.0, le=1.0, default=0.3)
    clarity: float = Field(ge=0.0, le=1.0, default=0.2)
    efficiency: float = Field(ge=0.0, le=1.0, default=0.1)


class GateConfig(BaseModel):
    """Full gate evaluation configuration."""

    model_config = ConfigDict(use_enum_values=True)

    gate_type: GateType = GateType.FULL
    strategy: GateStrategy = GateStrategy.HYBRID
    quorum_size: int = Field(ge=1, le=21, default=3)
    quorum_threshold: float = Field(ge=0.5, le=1.0, default=0.67)
    max_rounds: int = Field(ge=1, le=10, default=5)
    early_stop_confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    convergence_epsilon: float = Field(ge=0.0, le=0.5, default=0.05)
    score_threshold: float = Field(ge=0.0, le=1.0, default=0.7)
    early_stop_agreement_band: float = Field(ge=0.0, le=1.0, default=0.2)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    rubric: RubricWeights = Field(default_factory=RubricWeights)
    cost: CostConfig = Field(default_factory=CostConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)


class GatePreset:
    """Pre-configured gate configurations for common use cases."""

    @staticmethod
    def light() -> GateConfig:
        """LIGHT preset: single-round vote, 3 haiku judges, fast."""
        return GateConfig(
            gate_type=GateType.LIGHT,
            strategy=GateStrategy.VOTE,
            quorum_size=3,
            quorum_threshold=0.67,
            max_rounds=1,
            early_stop_confidence=0.85,
            cost=CostConfig(model_tier=ModelTier.HAIKU, max_total_tokens=30_000),
        )

    @staticmethod
    def full() -> GateConfig:
        """FULL preset: hybrid strategy, 5 sonnet judges, multi-round."""
        return GateConfig(
            gate_type=GateType.FULL,
            strategy=GateStrategy.HYBRID,
            quorum_size=5,
            quorum_threshold=0.67,
            max_rounds=5,
            early_stop_confidence=0.85,
            cost=CostConfig(model_tier=ModelTier.SONNET, max_total_tokens=100_000),
        )

    @staticmethod
    def critic() -> GateConfig:
        """CRITIC preset: debate + critic layer, 7 opus judges, rigorous."""
        return GateConfig(
            gate_type=GateType.CRITIC,
            strategy=GateStrategy.CRITIC,
            quorum_size=7,
            quorum_threshold=0.75,
            max_rounds=7,
            early_stop_confidence=0.90,
            cost=CostConfig(model_tier=ModelTier.OPUS, max_total_tokens=300_000),
            escalation=EscalationConfig(max_total_judges=13, max_escalation_rounds=3),
        )


class JudgeVote(BaseModel):
    """Individual judge vote in a gate evaluation."""

    model_config = ConfigDict(use_enum_values=True)

    judge_id: str
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    reasoning: str = ""
    round_number: int = Field(ge=1, default=1)


class EvaluationResult(BaseModel):
    """Result of a gate evaluation."""

    model_config = ConfigDict(use_enum_values=True)

    result: EvaluationOutcome
    confidence: float = Field(ge=0.0, le=1.0)
    agreement_ratio: float = Field(ge=0.0, le=1.0, default=1.0)
    rounds_used: int = Field(ge=0, default=1)
    judges_used: int = Field(ge=0, default=0)
    token_cost: int = Field(ge=0, default=0)
    reasoning: str = ""
    individual_scores: list[float] = Field(default_factory=list)
