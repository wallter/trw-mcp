"""TRW gate evaluation tool — adaptive gate evaluation via MCP.

PRD-QUAL-005-FR11: MCP tool for gate evaluation with preset loading,
strategy selection, and judge_fn injection.
"""

from __future__ import annotations

from typing import Callable

import structlog
from fastmcp import FastMCP

from trw_mcp.gate.strategies import get_strategy
from trw_mcp.models.gate import GateConfig, GatePreset, JudgeVote
from trw_mcp.state._paths import resolve_run_path
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

logger = structlog.get_logger()

_writer = FileStateWriter()
_events = FileEventLogger(_writer)

# Preset lookup — maps gate type strings to factory methods.
_PRESETS: dict[str, Callable[[], GateConfig]] = {
    "LIGHT": GatePreset.light,
    "FULL": GatePreset.full,
    "CRITIC": GatePreset.critic,
}


def _default_judge_fn(judge_id: str, shard_output: str, round_number: int) -> JudgeVote:
    """Placeholder judge using a length-based heuristic.

    In production this would be replaced by an LLM-based judge.
    Score range: [0.5, 0.8] based on output length (capped at 5000 chars).
    """
    length_score = min(1.0, len(shard_output) / 5000)
    score = 0.5 + length_score * 0.3

    return JudgeVote(
        judge_id=judge_id,
        score=round(score, 4),
        confidence=0.6,
        reasoning="Heuristic evaluation (LLM judge not available)",
        round_number=round_number,
    )


def _log_gate_event(
    run_path: str,
    gate_type: str,
    strategy_name: str,
    result: str,
    confidence: float,
    judges_used: int,
    rounds_used: int,
) -> None:
    """Best-effort event logging for a gate evaluation."""
    try:
        resolved = resolve_run_path(run_path)
        _events.log_event(
            resolved / "meta" / "events.jsonl",
            "gate_evaluation",
            {
                "gate_type": gate_type,
                "strategy": strategy_name,
                "result": result,
                "confidence": str(confidence),
                "judges_used": str(judges_used),
                "rounds_used": str(rounds_used),
            },
        )
    except (OSError, ValueError, KeyError):
        pass


def register_gate_tools(server: FastMCP) -> None:
    """Register gate evaluation tools on the MCP server."""

    @server.tool()
    def trw_gate_evaluate(
        gate_type: str = "FULL",
        shard_outputs: str = "",
        rubric_override: dict[str, float] | None = None,
        config_override: dict[str, object] | None = None,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Evaluate shard outputs through an adaptive gate.

        Loads a gate preset, applies overrides, selects strategy,
        executes evaluation, logs event, and returns result.

        Args:
            gate_type: Gate type preset — "LIGHT", "FULL", or "CRITIC".
            shard_outputs: Shard output text to evaluate.
            rubric_override: Optional rubric weight overrides.
            config_override: Optional gate config overrides.
            run_path: Optional run path for event logging.
        """
        preset_fn = _PRESETS.get(gate_type.upper(), GatePreset.full)
        gate_config = preset_fn()

        if config_override:
            safe_overrides = {
                k: v for k, v in config_override.items() if hasattr(gate_config, k)
            }
            if safe_overrides:
                gate_config = gate_config.model_copy(update=safe_overrides)

        if rubric_override:
            rubric = gate_config.rubric.model_copy(update=rubric_override)
            gate_config = gate_config.model_copy(update={"rubric": rubric})

        strategy_name = gate_config.strategy
        try:
            strategy = get_strategy(strategy_name)
        except ValueError as exc:
            return {"error": str(exc), "gate_type": gate_type}

        result = strategy.evaluate(shard_outputs, gate_config, _default_judge_fn)

        if run_path:
            _log_gate_event(
                run_path, gate_type, strategy_name,
                result.result, result.confidence,
                result.judges_used, result.rounds_used,
            )

        logger.info(
            "gate_evaluation_complete",
            gate_type=gate_type,
            strategy=strategy_name,
            result=result.result,
            confidence=result.confidence,
        )

        return {
            "gate_type": gate_type,
            "strategy": strategy_name,
            "result": result.result,
            "confidence": result.confidence,
            "agreement_ratio": result.agreement_ratio,
            "rounds_used": result.rounds_used,
            "judges_used": result.judges_used,
            "token_cost": result.token_cost,
            "reasoning": result.reasoning,
            "individual_scores": result.individual_scores,
        }
