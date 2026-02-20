"""TRW LLM usage reporting tool — PRD-CORE-020.

Reads .trw/logs/llm_usage.jsonl and aggregates token usage,
cost estimates, and call counts across sessions.
"""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()

_COST_RATES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}
_DEFAULT_RATE: dict[str, float] = {"input": 3.00, "output": 15.00}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute cost estimate in USD for a given model and token counts.

    Args:
        model: Model identifier string.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Cost estimate in USD, rounded to 6 decimal places.
    """
    rates = _COST_RATES.get(model, _DEFAULT_RATE)
    cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    return round(cost, 6)


def register_usage_tools(server: FastMCP) -> None:
    """Register LLM usage reporting tools on the MCP server."""

    @server.tool()
    def trw_usage_report(period: str = "all") -> dict[str, object]:
        """Aggregate LLM token usage and cost estimates from .trw/logs/llm_usage.jsonl.

        Reads all recorded LLM call records and returns totals, per-model
        breakdowns, and per-caller breakdowns. Useful for tracking API spend
        across sessions and tasks.

        Args:
            period: Aggregation period — only "all" is supported currently.
        """
        trw_dir = resolve_trw_dir()
        log_path = trw_dir / _config.logs_dir / _config.llm_usage_log_file

        records = _reader.read_jsonl(log_path)

        if not records:
            logger.info(
                "usage_report_empty",
                log_path=str(log_path),
            )
            return {
                "period": period,
                "log_path": str(log_path),
                "message": "No LLM usage data found",
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cost_estimate_usd": 0.0,
                "by_model": {},
                "by_caller": {},
            }

        total_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0.0

        by_model: dict[str, dict[str, object]] = {}
        by_caller: dict[str, dict[str, object]] = {}

        for record in records:
            model = str(record.get("model", "unknown"))
            input_tokens = int(str(record.get("input_tokens", 0)))
            output_tokens = int(str(record.get("output_tokens", 0)))
            caller = str(record.get("caller", "unknown"))

            cost = _compute_cost(model, input_tokens, output_tokens)

            total_calls += 1
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_cost += cost

            # Aggregate by model
            if model not in by_model:
                by_model[model] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_estimate_usd": 0.0,
                }
            model_entry = by_model[model]
            model_entry["calls"] = int(str(model_entry["calls"])) + 1
            model_entry["input_tokens"] = int(str(model_entry["input_tokens"])) + input_tokens
            model_entry["output_tokens"] = int(str(model_entry["output_tokens"])) + output_tokens
            model_entry["cost_estimate_usd"] = round(
                float(str(model_entry["cost_estimate_usd"])) + cost, 6
            )

            # Aggregate by caller
            if caller not in by_caller:
                by_caller[caller] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            caller_entry = by_caller[caller]
            caller_entry["calls"] = int(str(caller_entry["calls"])) + 1
            caller_entry["input_tokens"] = int(str(caller_entry["input_tokens"])) + input_tokens
            caller_entry["output_tokens"] = int(str(caller_entry["output_tokens"])) + output_tokens

        total_cost_rounded = round(total_cost, 6)

        logger.info(
            "usage_report_generated",
            total_calls=total_calls,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost_estimate_usd=total_cost_rounded,
        )

        return {
            "period": period,
            "log_path": str(log_path),
            "total_calls": total_calls,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_estimate_usd": total_cost_rounded,
            "by_model": by_model,
            "by_caller": by_caller,
        }
