"""Tests for gate evaluation strategies and MCP tool — PRD-QUAL-005 Phase 6.

All tests use deterministic mock judge_fn callables — no LLM calls.
"""

from __future__ import annotations

import pytest

from trw_mcp.gate.strategies import (
    CriticStrategy,
    DebateStrategy,
    HybridStrategy,
    VoteStrategy,
    get_strategy,
)
from trw_mcp.models.gate import (
    GatePreset,
    JudgeVote,
)


def _passing_judge(judge_id: str, shard_output: str, round_number: int) -> JudgeVote:
    return JudgeVote(
        judge_id=judge_id,
        score=0.9,
        confidence=0.85,
        reasoning="pass",
        round_number=round_number,
    )


def _failing_judge(judge_id: str, shard_output: str, round_number: int) -> JudgeVote:
    return JudgeVote(
        judge_id=judge_id,
        score=0.3,
        confidence=0.80,
        reasoning="fail",
        round_number=round_number,
    )


def _split_judge(judge_id: str, shard_output: str, round_number: int) -> JudgeVote:
    """Even-indexed judges pass, odd-indexed fail."""
    idx = int(judge_id.split("-")[-1]) if "-" in judge_id else 0
    score = 0.9 if idx % 2 == 0 else 0.3
    return JudgeVote(
        judge_id=judge_id,
        score=score,
        confidence=0.75,
        reasoning="split",
        round_number=round_number,
    )


class TestVoteStrategy:
    """VoteStrategy — single-round parallel vote."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.config = GatePreset.light()
        self.strategy = VoteStrategy()

    def test_unanimous_pass(self) -> None:
        result = self.strategy.evaluate("good output", self.config, _passing_judge)
        assert result.result == "pass"
        assert result.rounds_used == 1
        assert result.judges_used == self.config.quorum_size

    def test_unanimous_fail(self) -> None:
        result = self.strategy.evaluate("bad output", self.config, _failing_judge)
        assert result.result == "fail"
        assert result.rounds_used == 1

    def test_split_vote_escalates(self) -> None:
        # 3 judges: indices 0,1,2 -> scores 0.9,0.3,0.9 -> 2/3=0.6667 < 0.67
        result = self.strategy.evaluate("mixed output", self.config, _split_judge)
        assert result.result == "escalate"


class TestDebateStrategy:
    """DebateStrategy — multi-round debate with early stopping."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.config = GatePreset.full()
        self.strategy = DebateStrategy()

    def test_unanimous_early_stop(self) -> None:
        result = self.strategy.evaluate("good output", self.config, _passing_judge)
        assert result.result == "pass"
        assert result.rounds_used <= self.config.max_rounds

    def test_all_fail_debate(self) -> None:
        result = self.strategy.evaluate("bad output", self.config, _failing_judge)
        assert result.result == "fail"


class TestHybridStrategy:
    """HybridStrategy — vote-first, debate-on-split."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.config = GatePreset.full()
        self.strategy = HybridStrategy()

    def test_pass_skips_debate(self) -> None:
        result = self.strategy.evaluate("good output", self.config, _passing_judge)
        assert result.result == "pass"
        assert result.rounds_used == 1

    def test_fail_skips_debate(self) -> None:
        result = self.strategy.evaluate("bad output", self.config, _failing_judge)
        assert result.result == "fail"
        assert result.rounds_used == 1

    def test_split_triggers_debate(self) -> None:
        # 5 judges, split: idx 0,1,2,3,4 -> scores 0.9,0.3,0.9,0.3,0.9
        # 3/5 pass = 0.60, below quorum 0.67 -> escalate -> triggers debate
        result = self.strategy.evaluate("mixed output", self.config, _split_judge)
        assert result.rounds_used > 1
        assert "Hybrid" in result.reasoning


class TestCriticStrategy:
    """CriticStrategy — debate + critic + final judge."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.config = GatePreset.critic()
        self.strategy = CriticStrategy()

    def test_critic_layers_execute(self) -> None:
        result = self.strategy.evaluate("good output", self.config, _passing_judge)
        assert result.result == "pass"
        assert result.judges_used > self.config.quorum_size

    def test_critic_fail(self) -> None:
        result = self.strategy.evaluate("bad output", self.config, _failing_judge)
        assert result.result == "fail"


class TestGetStrategy:
    """Strategy registry lookup."""

    def test_valid_strategies(self) -> None:
        for name in ("vote", "debate", "hybrid", "critic"):
            strategy = get_strategy(name)
            assert hasattr(strategy, "evaluate")

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown"):
            get_strategy("unknown")


class TestGateEvaluateTool:
    """End-to-end MCP tool tests with mock judge_fn."""

    def test_default_judge_produces_valid_vote(self) -> None:
        from trw_mcp.tools.gate_strategy import _default_judge_fn

        vote = _default_judge_fn("test-judge", "some output", 1)
        assert isinstance(vote, JudgeVote)
        assert 0.0 <= vote.score <= 1.0
        assert vote.confidence > 0

    def test_tool_is_registered(self) -> None:
        from fastmcp import FastMCP

        from trw_mcp.tools.gate_strategy import register_gate_tools

        test_server = FastMCP("test")
        register_gate_tools(test_server)
        tools = test_server._tool_manager._tools
        assert "trw_gate_evaluate" in tools

    def test_default_judge_heuristic_range(self) -> None:
        from trw_mcp.tools.gate_strategy import _default_judge_fn

        short_vote = _default_judge_fn("j1", "hi", 1)
        assert 0.5 <= short_vote.score <= 0.8

        long_vote = _default_judge_fn("j2", "x" * 5000, 1)
        assert 0.5 <= long_vote.score <= 0.8
