"""Tests for gate models — presets, config, enums, TRWConfig fields.

PRD-QUAL-005: Adaptive gate evaluation models.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.gate import (
    CostConfig,
    EvaluationResult,
    GateConfig,
    GatePreset,
    GateStrategy,
    JudgeVote,
    ModelTier,
)


class TestGatePresets:
    def test_light_preset(self) -> None:
        config = GatePreset.light()
        assert config.gate_type == "LIGHT"
        assert config.strategy == "vote"
        assert config.quorum_size == 3
        assert config.max_rounds == 1
        assert config.cost.model_tier == "haiku"

    def test_full_preset(self) -> None:
        config = GatePreset.full()
        assert config.gate_type == "FULL"
        assert config.strategy == "hybrid"
        assert config.quorum_size == 5
        assert config.max_rounds == 5
        assert config.cost.model_tier == "sonnet"

    def test_critic_preset(self) -> None:
        config = GatePreset.critic()
        assert config.gate_type == "CRITIC"
        assert config.strategy == "critic"
        assert config.quorum_size == 7
        assert config.quorum_threshold == 0.75
        assert config.cost.model_tier == "opus"
        assert config.escalation.max_total_judges == 13


class TestGateConfigOverride:
    def test_override_quorum(self) -> None:
        base = GatePreset.full()
        overridden = base.model_copy(update={"quorum_size": 7, "quorum_threshold": 0.75})
        assert overridden.quorum_size == 7
        assert overridden.quorum_threshold == 0.75
        assert overridden.strategy == "hybrid"


class TestGateEnumSerialization:
    def test_gate_type_serialized(self) -> None:
        config = GateConfig()
        assert config.gate_type == "FULL"
        assert isinstance(config.gate_type, str)

    def test_strategy_serialized(self) -> None:
        config = GateConfig(strategy=GateStrategy.DEBATE)
        assert config.strategy == "debate"

    def test_model_tier_serialized(self) -> None:
        cost = CostConfig(model_tier=ModelTier.OPUS)
        assert cost.model_tier == "opus"


class TestJudgeVote:
    def test_create(self) -> None:
        vote = JudgeVote(judge_id="j1", score=0.85, confidence=0.9, reasoning="Good")
        assert vote.score == 0.85
        assert vote.round_number == 1

    def test_score_bounds(self) -> None:
        with pytest.raises(Exception):
            JudgeVote(judge_id="j1", score=1.5)


class TestEvaluationResult:
    def test_pass_result(self) -> None:
        result = EvaluationResult(
            result="pass",
            confidence=0.92,
            agreement_ratio=0.85,
            rounds_used=2,
            judges_used=5,
            individual_scores=[0.9, 0.85, 0.88, 0.92, 0.87],
        )
        assert result.result == "pass"
        assert len(result.individual_scores) == 5


class TestTRWConfigGateFields:
    def test_defaults(self) -> None:
        config = TRWConfig()
        assert config.gate_default_type == "FULL"
        assert config.gate_strategy == "hybrid"
        assert config.gate_early_stop_confidence == 0.85
        assert config.gate_max_rounds == 5
        assert config.gate_convergence_epsilon == 0.05
        assert config.gate_escalation_enabled is True
        assert config.gate_max_total_judges == 13

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_GATE_MAX_ROUNDS", "10")
        config = TRWConfig()
        assert config.gate_max_rounds == 10
