"""TRW MCP Server -- orchestration, requirements, and self-learning tools.

FastMCP server entry point. Registers all tools, resources, and prompts.
Run with: ``trw-mcp`` CLI or ``trw-mcp --debug`` for file logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig

_NOISY_LOGGERS = ("fastmcp", "redis", "httpcore", "httpx", "asyncio")


def _configure_logging(*, debug: bool, config: TRWConfig) -> None:
    """Configure structlog processors and stdlib logging.

    Args:
        debug: When True, enables file logging to .trw/logs/ and
            dev-friendly console output on stderr at DEBUG level.
        config: TRW configuration for path resolution.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.contextvars.merge_contextvars,
        structlog.processors.StackInfoRenderer(),
    ]

    if debug:
        logs_dir = Path.cwd() / config.trw_dir / config.logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = logs_dir / f"trw-mcp-{today}.jsonl"

        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)

        logging.basicConfig(
            format="%(message)s",
            level=logging.DEBUG,
            handlers=[file_handler, stderr_handler],
            force=True,
        )

        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            logger_factory=structlog.stdlib.LoggerFactory(),
        )

        # FastMCP's Docket worker logs every Redis command (~1.25M lines/day),
        # producing 145 MB of noise vs ~800 lines of actual TRW events.
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

    else:
        logging.basicConfig(
            format="%(message)s",
            level=logging.INFO,
            handlers=[logging.StreamHandler(sys.stderr)],
            force=True,
        )

        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            logger_factory=structlog.stdlib.LoggerFactory(),
        )


mcp = FastMCP(
    "trw",
    instructions=(
        "TRW orchestration + self-learning tools. "
        "Workflow: trw_init → trw_event/checkpoint → trw_reflect → trw_recall → trw_claude_md_sync. "
        ".trw/ persists knowledge across sessions. "
        "Execute trw_recall('*', min_impact=0.7) at session start to load high-impact learnings. "
        "Execute trw_reflect after completing tasks. "
        "Execute trw_claude_md_sync at delivery. "
        "Read .trw/frameworks/FRAMEWORK.md for phase requirements. "
        "Sub-agents: call trw_shard_context first to get run paths and tool guidance."
    ),
)


def _register_tools() -> None:
    """Register all tools, resources, and prompts on the MCP server."""
    # Tools (alphabetical)
    from trw_mcp.tools.bdd import register_bdd_tools
    from trw_mcp.tools.compliance import register_compliance_tools
    from trw_mcp.tools.findings import register_findings_tools
    from trw_mcp.tools.gate_strategy import register_gate_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.refactoring import register_refactoring_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.testing import register_testing_tools
    from trw_mcp.tools.tracks import register_track_tools
    from trw_mcp.tools.velocity import register_velocity_tools
    from trw_mcp.tools.wave import register_wave_tools

    # Resources
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources

    # Prompts
    from trw_mcp.prompts.aaref import register_aaref_prompts

    # --- Register tools (alphabetical) ---
    register_bdd_tools(mcp)
    register_compliance_tools(mcp)
    register_findings_tools(mcp)
    register_gate_tools(mcp)
    register_learning_tools(mcp)
    register_orchestration_tools(mcp)
    register_refactoring_tools(mcp)
    register_requirements_tools(mcp)
    register_testing_tools(mcp)
    register_track_tools(mcp)
    register_velocity_tools(mcp)
    register_wave_tools(mcp)

    # --- Register resources (alphabetical) ---
    register_config_resources(mcp)
    register_run_state_resources(mcp)
    register_template_resources(mcp)

    # --- Register prompts ---
    register_aaref_prompts(mcp)


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()


def main() -> None:
    """Entry point for the trw-mcp CLI command."""
    parser = argparse.ArgumentParser(
        prog="trw-mcp",
        description="TRW Framework MCP Server",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging to .trw/logs/ and stderr",
    )
    args = parser.parse_args()

    config = TRWConfig()
    debug = args.debug or config.debug

    _configure_logging(debug=debug, config=config)

    logger = structlog.get_logger()

    logger.info(
        "trw_server_initialized",
        tools_registered=True,
        debug_mode=debug,
    )

    mcp.run()


if __name__ == "__main__":
    main()
