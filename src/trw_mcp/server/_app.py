"""FastMCP application creation and configuration.

Creates the ``mcp`` FastMCP instance with middleware, instructions,
and structured logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp._logging import configure_logging
from trw_mcp.meta_tune.boot_checks import validate_defaults as validate_meta_tune_defaults
from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

_DEFAULT_INSTRUCTIONS = (
    "TRW turns session history into reusable engineering context. "
    "Call trw_session_start() first: it restores prior learnings and any active run, "
    "cutting repeat investigation by ~30%. "
    "Workflow: plan, implement, verify, deliver. "
    "Read .trw/frameworks/FRAMEWORK.md after startup or compaction for phase gates. "
    "Use trw_learn() when you discover a root cause or durable pattern. "
    "Finish with trw_deliver() so progress and maintenance persist for future sessions."
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


def _try_init_mcp_security(config: TRWConfig) -> object | None:
    """Initialize the mounted MCP security middleware."""
    from trw_mcp.startup import init_security

    return init_security(config.security.mcp)


def _try_init_response_optimizer() -> object | None:
    """Try to initialize ResponseOptimizerMiddleware. Returns None on failure."""
    try:
        from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

        return ResponseOptimizerMiddleware()
    except Exception:  # justified: fail-open, response optimizer is optional enhancement
        logger.warning("middleware_init_failed", component="ResponseOptimizerMiddleware")
        return None


def _run_meta_tune_boot_validation(config: TRWConfig) -> None:
    """Fail-loud SAFE-001 boot validation when meta-tune is enabled."""
    if config.meta_tune.enabled:
        validate_meta_tune_defaults(config)


def _build_middleware() -> list[object]:
    """Build the middleware list, conditionally including progressive disclosure.

    Each middleware component is initialized by a dedicated helper that
    returns None on failure (fail-open). This keeps the orchestration
    logic readable while isolating error handling per component.
    """
    config = _try_load_config()
    if config is None:
        config = TRWConfig()
    _run_meta_tune_boot_validation(config)

    middleware: list[object] = []

    global _mcp_security
    _mcp_security = _try_init_mcp_security(config)
    if _mcp_security is not None:
        middleware.append(_mcp_security)

    ceremony = _try_init_ceremony()
    if ceremony is not None:
        middleware.append(ceremony)

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


@asynccontextmanager
async def _build_sync_lifespan(_: FastMCP) -> AsyncIterator[None]:
    """Start the background sync client when backend sync is configured."""
    sync_task: asyncio.Task[None] | None = None
    config = _try_load_config()
    try:
        if config is not None:
            backend_url = config.resolved_backend_url
            backend_api_key = config.resolved_backend_api_key
            if backend_url and backend_api_key:
                if config.backend_url and config.backend_api_key:
                    source = "explicit"
                elif config.backend_url or config.backend_api_key:
                    source = "mixed"
                else:
                    source = "platform_fallback"
                logger.info("sync_config_resolved", source=source, url=backend_url)

                from trw_mcp.state._paths import resolve_trw_dir
                from trw_mcp.sync.client import BackendSyncClient

                sync_client = BackendSyncClient(config=config, trw_dir=resolve_trw_dir())
                sync_task = asyncio.create_task(sync_client.run_sync_loop())
            else:
                logger.info("sync_config_resolved", source="none")
        yield
    finally:
        if sync_task is not None:
            sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await sync_task


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
        lifespan=_build_sync_lifespan,
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


# ── Module-level singletons ─────────────────────────────────────────
# _mcp_security holds the MCPSecurityMiddleware instance (PRD-INFRA-SEC-001
# FR-6/FR-9) when startup succeeds; it is None otherwise (observe-mode
# fail-open). Transports consult this for per-dispatch security events.
_mcp_security: object | None = None
mcp = create_app()
_middleware_list: list[object] = list(mcp.middleware)  # backward compat for _tools.py
