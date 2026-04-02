"""TRW LLM usage reporting tool — PRD-CORE-020.

Reads .trw/logs/llm_usage.jsonl and aggregates token usage,
cost estimates, and call counts across sessions.
"""

from __future__ import annotations

from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts._tools import (
    ProgressiveExpandResult,
    UsageCallerEntryDict,
    UsageGroupEntryDict,
    UsageModelEntryDict,
    UsageReportResult,
)
from trw_mcp.models.typed_dicts._trust import TrustLevelQueryResult
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.progressive_middleware import ProgressiveDisclosureMiddleware
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

_progressive_middleware: ProgressiveDisclosureMiddleware | None = None

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

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_usage_report(
        period: str = "all",
        group_by: str = "none",
    ) -> UsageReportResult:
        """Track your LLM API spend — total tokens, costs, and breakdowns by model and caller.

        Reads .trw/logs/llm_usage.jsonl and aggregates token usage, cost estimates,
        and call counts. Useful for understanding which operations consume the most
        tokens and optimizing accordingly.

        Args:
            period: Aggregation period — only "all" is supported currently.
            group_by: Group results by field — "agent", "phase", "model",
                "task", or "none" (default). When not "none", adds a
                "grouped_by" breakdown dict to the response.
        """
        _VALID_GROUP_BY = {"agent", "phase", "model", "task", "none"}
        if group_by not in _VALID_GROUP_BY:
            raise ValueError(f"group_by must be one of: {', '.join(sorted(_VALID_GROUP_BY))}")

        config = get_config()
        reader = FileStateReader()
        trw_dir = resolve_trw_dir()
        log_path = trw_dir / config.logs_dir / config.llm_usage_log_file

        records = reader.read_jsonl(log_path)

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

        by_model: dict[str, UsageModelEntryDict] = {}
        by_caller: dict[str, UsageCallerEntryDict] = {}

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
            model_entry["calls"] = model_entry["calls"] + 1
            model_entry["input_tokens"] = model_entry["input_tokens"] + input_tokens
            model_entry["output_tokens"] = model_entry["output_tokens"] + output_tokens
            model_entry["cost_estimate_usd"] = round(model_entry["cost_estimate_usd"] + cost, 6)

            # Aggregate by caller
            if caller not in by_caller:
                by_caller[caller] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            caller_entry = by_caller[caller]
            caller_entry["calls"] = caller_entry["calls"] + 1
            caller_entry["input_tokens"] = caller_entry["input_tokens"] + input_tokens
            caller_entry["output_tokens"] = caller_entry["output_tokens"] + output_tokens

        total_cost_rounded = round(total_cost, 6)

        logger.info(
            "usage_report_ok",
            period=period,
            tool_calls=total_calls,
            sessions=len({str(r.get("session_id", r.get("caller", ""))) for r in records}),
        )
        _top_tools = sorted(by_caller.items(), key=lambda kv: kv[1]["calls"], reverse=True)[:5]
        logger.debug("usage_detail", top_tools=[k for k, _ in _top_tools])
        logger.info(
            "usage_report_generated",
            total_calls=total_calls,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost_estimate_usd=total_cost_rounded,
        )

        result: UsageReportResult = {
            "period": period,
            "log_path": str(log_path),
            "total_calls": total_calls,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_estimate_usd": total_cost_rounded,
            "by_model": by_model,
            "by_caller": by_caller,
        }

        # Group-by breakdown (INFRA-029 FR02)
        if group_by != "none":
            # Map group_by value to the JSONL field name
            field_map: dict[str, str] = {
                "agent": "agent_id",
                "phase": "phase",
                "model": "model",
                "task": "task",
            }
            field_key = field_map.get(group_by, group_by)
            grouped: dict[str, UsageGroupEntryDict] = {}
            for record in records:
                bucket = str(record.get(field_key, "unknown"))
                if bucket not in grouped:
                    grouped[bucket] = {
                        "calls": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_estimate_usd": 0.0,
                    }
                entry = grouped[bucket]
                rec_input = int(str(record.get("input_tokens", 0)))
                rec_output = int(str(record.get("output_tokens", 0)))
                rec_model = str(record.get("model", "unknown"))
                entry["calls"] = entry["calls"] + 1
                entry["input_tokens"] = entry["input_tokens"] + rec_input
                entry["output_tokens"] = entry["output_tokens"] + rec_output
                entry["cost_estimate_usd"] = round(
                    entry["cost_estimate_usd"] + _compute_cost(rec_model, rec_input, rec_output),
                    6,
                )
            result["group_by"] = group_by
            result["grouped_by"] = grouped

        return result

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_progressive_expand(group: str) -> ProgressiveExpandResult:
        """Expand a capability group so its tools show full schemas.

        When progressive disclosure is enabled, non-hot-set tools only show
        compact capability cards. Call this to expand a whole group at once.

        Args:
            group: Group name (ceremony, learning, orchestration,
                requirements, build).
        """
        from trw_mcp.state.usage_profiler import TOOL_GROUPS

        if _progressive_middleware is None:
            tools = TOOL_GROUPS.get(group, [])
            return {
                "group": group,
                "expanded_tools": [],
                "already_expanded": tools,
            }

        newly, already = _progressive_middleware.expand_group(group)
        return {
            "group": group,
            "expanded_tools": newly,
            "already_expanded": already,
        }

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_trust_level(
        security_tags: list[str] | None = None,
    ) -> TrustLevelQueryResult:
        """Query your project's trust tier — Crawl/Walk/Run graduated autonomy.

        Returns the current trust level based on accumulated successful sessions.
        Optionally evaluates whether a change with the given security tags requires
        human review.

        Args:
            security_tags: Optional list of security tags (e.g. ["auth", "secrets"])
                to evaluate review requirements for a specific change.
        """
        from trw_mcp.state.trust import requires_human_review, trust_level_calculate

        trw_dir = resolve_trw_dir()
        trust = trust_level_calculate(trw_dir)
        result: TrustLevelQueryResult = {
            "tier": trust["tier"],
            "session_count": trust["session_count"],
            "review_mode": trust["review_mode"],
            "review_sample_rate": trust["review_sample_rate"],
            "locked": trust["locked"],
            "lock_reason": trust["lock_reason"],
        }

        if security_tags:
            review = requires_human_review(security_tags, [], cast("dict[str, object]", trust))
            result["review_required"] = review["required"]
            result["review_reason"] = review["reason"]

        return result


def set_progressive_middleware(mw: ProgressiveDisclosureMiddleware | None) -> None:
    """Set the progressive disclosure middleware reference for expand tool."""
    global _progressive_middleware
    _progressive_middleware = mw
