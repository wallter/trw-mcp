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
    "TRW gives you engineering memory that compounds across sessions. "
    "Agents that record discoveries solve 30% more problems. "
    "Workflow: understand the task \u2192 plan your approach \u2192 implement \u2192 verify. "
    "Three essential calls: "
    "trw_session_start() loads prior learnings so you don't repeat solved problems. "
    "trw_learn() records what you discovered \u2014 the root cause, the fix, the gotcha. "
    "trw_deliver() persists everything for future sessions. "
    "Without trw_deliver, your learnings die with your context window."
)


def _load_server_instructions() -> str:
    """Load MCP server instructions from centralized messages, with fallback."""
    try:
        from trw_mcp.prompts.messaging import get_message_or_default

        return get_message_or_default("server_instructions", _DEFAULT_INSTRUCTIONS)
    except Exception:  # justified: fail-open, message registry failure falls back to inline default
        return _DEFAULT_INSTRUCTIONS


def _try_init_ceremony() -> CeremonyMiddleware | None:
    """Try to initialize CeremonyMiddleware. Returns None on failure (fail-open)."""
    try:
        return CeremonyMiddleware()
    except Exception:  # justified: fail-open, middleware init failure must not crash server startup
        logger.warning("middleware_init_failed", component="CeremonyMiddleware")
        return None


def _try_load_config() -> TRWConfig | None:
    """Try to load TRWConfig. Returns None on failure (fail-open)."""
    try:
        from trw_mcp.models.config import get_config

        return get_config()
    except Exception:  # justified: fail-open, config load failure must not crash server startup
        logger.warning("middleware_config_load_failed", component="get_config")
        return None


def _try_init_progressive(config: TRWConfig) -> object | None:
    """Try to initialize ProgressiveDisclosureMiddleware. Returns None on failure."""
    if not config.progressive_disclosure:
        return None
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.progressive_middleware import ProgressiveDisclosureMiddleware
        from trw_mcp.state.usage_profiler import TOOL_GROUPS, compute_hot_set

        trw_dir = resolve_trw_dir()
        hot_set = set(compute_hot_set(trw_dir))
        return ProgressiveDisclosureMiddleware(hot_set=hot_set, tool_groups=TOOL_GROUPS)
    except Exception:  # justified: fail-open, progressive disclosure is optional enhancement
        logger.warning("middleware_init_failed", component="ProgressiveDisclosureMiddleware")
        return None


def _try_init_observation_masking(config: TRWConfig) -> object | None:
    """Try to initialize ContextBudgetMiddleware. Returns None on failure."""
    if not config.observation_masking:
        return None
    try:
        from trw_mcp.middleware.context_budget import ContextBudgetMiddleware

        return ContextBudgetMiddleware()
    except Exception:  # justified: fail-open, observation masking is optional enhancement
        logger.warning("middleware_init_failed", component="ContextBudgetMiddleware")
        return None


def _try_init_response_optimizer() -> object | None:
    """Try to initialize ResponseOptimizerMiddleware. Returns None on failure."""
    try:
        from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

        return ResponseOptimizerMiddleware()
    except Exception:  # justified: fail-open, response optimizer is optional enhancement
        logger.warning("middleware_init_failed", component="ResponseOptimizerMiddleware")
        return None


def _build_middleware() -> list[object]:
    """Build the middleware list, conditionally including progressive disclosure.

    Each middleware component is initialized by a dedicated helper that
    returns None on failure (fail-open). This keeps the orchestration
    logic readable while isolating error handling per component.
    """
    ceremony = _try_init_ceremony()
    if ceremony is None:
        return []
    middleware: list[object] = [ceremony]

    config = _try_load_config()
    if config is None:
        return middleware

    middleware.extend(
        mw
        for mw in (
            _try_init_progressive(config),
            _try_init_observation_masking(config),
            _try_init_response_optimizer(),
        )
        if mw is not None
    )

    return middleware


def _resolve_instructions(instructions: str | None) -> str:
    """Resolve server instructions, respecting the MCP instructions gate.

    PRD-CORE-125-FR04: When ``effective_mcp_instructions_enabled`` is False,
    return an empty string so no instructions are injected into the MCP server
    response.  Fail-open: if config loading fails, use the default instructions.
    """
    if instructions is not None:
        return instructions
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if not config.effective_mcp_instructions_enabled:
            logger.debug("surface_gated", surface="mcp_instructions")
            return ""
    except Exception:  # justified: fail-open, config failure uses default instructions
        logger.debug("mcp_instructions_gate_unavailable", exc_info=True)
    return _load_server_instructions()


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
        instructions=_resolve_instructions(instructions),
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
