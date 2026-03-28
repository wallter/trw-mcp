"""FastMCP application creation and configuration.

Creates the ``mcp`` FastMCP instance with middleware, instructions,
and structured logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp._logging import configure_logging
from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

_DEFAULT_INSTRUCTIONS = (
    "TRW gives you engineering memory that persists across sessions "
    "\u2014 patterns, gotchas, and project knowledge that accumulate over time. "
    "Call trw_session_start() first to load your prior learnings and any active run state. "
    "Pass a query for focused recall: trw_session_start(query='auth patterns'). "
    "Read .trw/frameworks/FRAMEWORK.md \u2014 it defines the 6-phase execution model, "
    "exit criteria, formations, and quality gates that your tools implement. "
    "Re-read it after context compaction. "
    "Workflow: trw_session_start \u2192 work \u2192 trw_learn (discoveries) \u2192 trw_deliver. "
    "Without trw_deliver, your learnings from this session are lost to future agents."
)


def _load_server_instructions() -> str:
    """Load MCP server instructions from centralized messages, with fallback."""
    try:
        from trw_mcp.prompts.messaging import get_message_or_default

        return get_message_or_default("server_instructions", _DEFAULT_INSTRUCTIONS)
    except Exception:  # justified: fail-open, message registry failure falls back to inline default
        return _DEFAULT_INSTRUCTIONS


def _build_middleware() -> list[object]:
    """Build the middleware list, conditionally including progressive disclosure.

    Catches all exceptions to prevent module-level import from crashing
    the server before logging is configured.
    """
    try:
        middleware: list[object] = [CeremonyMiddleware()]
    except Exception:  # justified: fail-open, middleware init failure must not crash server startup
        logger.warning("middleware_init_failed", component="CeremonyMiddleware")
        return []

    # Load config once for all optional middleware
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
    except Exception:  # justified: fail-open, config load failure must not crash server startup
        logger.warning("middleware_config_load_failed", component="get_config")
        return middleware

    # Progressive disclosure
    if config.progressive_disclosure:
        try:
            from trw_mcp.state._paths import resolve_trw_dir
            from trw_mcp.state.progressive_middleware import ProgressiveDisclosureMiddleware
            from trw_mcp.state.usage_profiler import TOOL_GROUPS, compute_hot_set

            trw_dir = resolve_trw_dir()
            hot_set = set(compute_hot_set(trw_dir))
            pd_mw = ProgressiveDisclosureMiddleware(hot_set=hot_set, tool_groups=TOOL_GROUPS)
            middleware.append(pd_mw)
        except Exception:  # justified: fail-open, progressive disclosure is optional enhancement
            logger.warning("middleware_init_failed", component="ProgressiveDisclosureMiddleware")

    # Observation masking: reduce verbosity in long sessions.
    if config.observation_masking:
        try:
            from trw_mcp.middleware.context_budget import ContextBudgetMiddleware

            middleware.append(ContextBudgetMiddleware())
        except Exception:  # justified: fail-open, observation masking is optional enhancement
            logger.warning("middleware_init_failed", component="ContextBudgetMiddleware")

    # Response optimizer: compact JSON (round floats, strip nulls/empties).
    # Added last so it runs on the final response after all other middleware.
    try:
        from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

        middleware.append(ResponseOptimizerMiddleware())
    except Exception:  # justified: fail-open, response optimizer is optional enhancement
        logger.warning("middleware_init_failed", component="ResponseOptimizerMiddleware")

    return middleware


def create_app(
    *,
    instructions: str | None = None,
    middleware: list[object] | None = None,
) -> FastMCP:
    """Create a new FastMCP application instance.

    Args:
        instructions: Override server instructions. Uses centralized messages by default.
        middleware: Override middleware list. Uses default chain by default.

    Returns:
        Configured FastMCP instance.
    """
    return FastMCP(
        "trw",
        instructions=instructions or _load_server_instructions(),
        middleware=middleware if middleware is not None else _build_middleware(),  # type: ignore[arg-type]
    )


def configure_logging_compat(*, debug: bool, config: TRWConfig) -> None:
    """Legacy-compatible wrapper for configure_logging.

    Translates the old (debug, config) signature to the new unified interface.
    Kept for backward compatibility with tests and internal callers.
    """
    log_dir: Path | None = None
    if debug:
        log_dir = Path.cwd() / config.trw_dir / config.logs_dir

    configure_logging(
        debug=debug,
        log_dir=log_dir,
        package_name="trw-mcp",
    )


# ── Module-level singleton (backward compat) ─────────────────────────
mcp = create_app()
_middleware_list: list[object] = list(mcp.middleware)  # backward compat for _tools.py
