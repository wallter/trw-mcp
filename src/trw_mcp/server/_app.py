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
        sys.stderr.write("WARNING: CeremonyMiddleware init failed, using empty middleware\n")
        return []

    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if config.progressive_disclosure:
            from trw_mcp.state._paths import resolve_trw_dir
            from trw_mcp.state.progressive_middleware import ProgressiveDisclosureMiddleware
            from trw_mcp.state.usage_profiler import TOOL_GROUPS, compute_hot_set

            trw_dir = resolve_trw_dir()
            hot_set = set(compute_hot_set(trw_dir))
            pd_mw = ProgressiveDisclosureMiddleware(hot_set=hot_set, tool_groups=TOOL_GROUPS)
            middleware.append(pd_mw)
    except Exception:  # justified: fail-open, progressive disclosure is optional enhancement
        sys.stderr.write("WARNING: Progressive disclosure middleware init failed, skipping\n")

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
            "fastmcp",
            "redis",
            "redis.asyncio",
            "redis.connection",
            "httpcore",
            "httpx",
            "asyncio",
            "urllib3",
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
