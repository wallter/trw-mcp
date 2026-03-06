"""FastMCP application creation and configuration.

Creates the ``mcp`` FastMCP instance with middleware, instructions,
and structured logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.models.config import TRWConfig

_DEFAULT_INSTRUCTIONS = (
    "TRW gives you engineering memory that persists across sessions "
    "\u2014 patterns, gotchas, and project knowledge that accumulate over time. "
    "Call trw_session_start() first to load your prior learnings and any active run state. "
    "Workflow: trw_session_start \u2192 work \u2192 trw_learn (discoveries) \u2192 trw_deliver. "
    "Without trw_deliver, your learnings from this session are lost to future agents."
)


def _load_server_instructions() -> str:
    """Load MCP server instructions from centralized messages, with fallback."""
    from trw_mcp.prompts.messaging import get_message_or_default

    return get_message_or_default("server_instructions", _DEFAULT_INSTRUCTIONS)


def _build_middleware() -> list[object]:
    """Build the middleware list, conditionally including progressive disclosure."""
    from trw_mcp.models.config import get_config

    middleware: list[object] = [CeremonyMiddleware()]

    config = get_config()
    if config.progressive_disclosure:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.progressive_middleware import ProgressiveDisclosureMiddleware
        from trw_mcp.state.usage_profiler import TOOL_GROUPS, compute_hot_set

        trw_dir = resolve_trw_dir()
        hot_set = set(compute_hot_set(trw_dir))
        pd_mw = ProgressiveDisclosureMiddleware(hot_set=hot_set, tool_groups=TOOL_GROUPS)
        middleware.append(pd_mw)

    return middleware


def configure_logging(*, debug: bool, config: TRWConfig) -> None:
    """Configure structlog processors and stdlib logging.

    Args:
        debug: When True, enables file logging to .trw/logs/ and
            dev-friendly console output on stderr at DEBUG level.
        config: TRW configuration for path resolution.
    """
    log_level = logging.DEBUG if debug else logging.INFO

    base_processors: list[structlog.types.Processor] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.contextvars.merge_contextvars,
        structlog.processors.StackInfoRenderer(),
    ]

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    if debug:
        logs_dir = Path.cwd() / config.trw_dir / config.logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = logs_dir / f"trw-mcp-{today}.jsonl"

        handlers.append(logging.FileHandler(str(log_file), encoding="utf-8"))
        base_processors.append(structlog.processors.format_exc_info)

        # Suppress FastMCP / Redis / HTTP noise (~1.25M lines/day, 145 MB vs ~800 TRW events)
        for logger_name in (
            "fastmcp", "redis", "redis.asyncio", "redis.connection",
            "httpcore", "httpx", "asyncio", "urllib3",
        ):
            logging.getLogger(logger_name).setLevel(logging.WARNING)

        # Filter non-JSON lines from file handler (catches raw Redis >>> protocol output)
        class _JsonOnlyFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                msg = str(record.getMessage())
                return msg.startswith(("{", "["))

        for handler in handlers:
            if isinstance(handler, logging.FileHandler):
                handler.addFilter(_JsonOnlyFilter())

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
        force=True,
    )

    structlog.configure(
        processors=[
            *base_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


# ── Module-level singleton ──────────────────────────────────────────────
_middleware_list = _build_middleware()

mcp = FastMCP(
    "trw",
    instructions=_load_server_instructions(),
    middleware=_middleware_list,  # type: ignore[arg-type]
)
