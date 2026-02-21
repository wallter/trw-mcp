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

from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.models.config import TRWConfig  # used in main()


def _configure_logging(*, debug: bool, config: TRWConfig) -> None:
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
                return msg.startswith("{") or msg.startswith("[")

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


mcp = FastMCP(
    "trw",
    instructions=(
        "TRW engineering memory + build verification. "
        "MANDATORY FIRST CALL: trw_session_start(). "
        "Workflow: trw_session_start → work → trw_learn (discoveries) → trw_deliver. "
        ".trw/ persists knowledge across sessions."
    ),
    middleware=[CeremonyMiddleware()],
)


def _register_tools() -> None:
    """Register all tools, resources, and prompts on the MCP server."""
    from trw_mcp.prompts.aaref import register_aaref_prompts
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.report import register_report_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.usage import register_usage_tools

    register_build_tools(mcp)
    register_ceremony_tools(mcp)
    register_learning_tools(mcp)
    register_orchestration_tools(mcp)
    register_report_tools(mcp)
    register_requirements_tools(mcp)
    register_usage_tools(mcp)

    register_config_resources(mcp)
    register_run_state_resources(mcp)
    register_template_resources(mcp)

    register_aaref_prompts(mcp)


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()


def _run_init_project(args: argparse.Namespace) -> None:
    """Handle the ``init-project`` subcommand."""
    from trw_mcp.bootstrap import init_project

    target = Path(args.target_dir).resolve()
    result = init_project(target, force=args.force)

    for f in result["created"]:
        print(f"  Created: {f}")
    for f in result["skipped"]:
        print(f"  Skipped (exists): {f}")
    for e in result["errors"]:
        print(f"  ERROR: {e}")

    if not result["errors"]:
        print(f"\nTRW framework initialized in {target}")
        print("Next steps:")
        print("  1. Edit CLAUDE.md with your project details")
        print("  2. Run `trw-mcp` to start the MCP server")
        print("  3. In Claude Code, run /mcp to connect")
        print("  4. Call trw_session_start() to begin")

    sys.exit(1 if result["errors"] else 0)


def _run_update_project(args: argparse.Namespace) -> None:
    """Handle the ``update-project`` subcommand."""
    from trw_mcp.bootstrap import update_project

    target = Path(args.target_dir).resolve()
    result = update_project(target, pip_install=args.pip_install)

    for f in result["updated"]:
        print(f"  Updated: {f}")
    for f in result["created"]:
        print(f"  Created (new): {f}")
    for f in result["preserved"]:
        print(f"  Preserved: {f}")
    for w in result.get("warnings", []):
        print(f"  WARNING: {w}")
    for e in result["errors"]:
        print(f"  ERROR: {e}")

    total = len(result["updated"]) + len(result["created"])
    if not result["errors"]:
        print(f"\nTRW framework updated in {target} ({total} files)")

    sys.exit(1 if result["errors"] else 0)


def _run_audit(args: argparse.Namespace) -> None:
    """Handle the ``audit`` subcommand."""
    from trw_mcp.audit import format_markdown, run_audit

    target = Path(args.target_dir).resolve()
    result = run_audit(target, fix=args.fix)

    if result.get("status") == "failed":
        print(f"  ERROR: {result.get('error', 'unknown')}")
        sys.exit(1)

    if args.format == "json":
        import json

        output = json.dumps(result, indent=2, default=str)
    else:
        output = format_markdown(result)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"  Audit report written to: {out_path}")
    else:
        print(output)

    sys.exit(0)


def _run_export(args: argparse.Namespace) -> None:
    """Handle the ``export`` subcommand."""
    import json

    from trw_mcp.export import export_data

    target = Path(args.target_dir).resolve()
    result = export_data(
        target,
        args.scope,
        fmt=args.format,
        since=getattr(args, "since", None),
        min_impact=getattr(args, "min_impact", 0.0),
    )

    if result.get("status") == "failed":
        print(f"  ERROR: {result.get('error', 'unknown')}")
        sys.exit(1)

    # CSV output for learnings
    if args.format == "csv" and "learnings_csv" in result:
        output = str(result["learnings_csv"])
    else:
        output = json.dumps(result, indent=2, default=str)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"  Export written to: {out_path}")
    else:
        print(output)

    sys.exit(0)


def _run_import_learnings(args: argparse.Namespace) -> None:
    """Handle the ``import-learnings`` subcommand."""
    from trw_mcp.export import import_learnings

    source = Path(args.source_file).resolve()
    target = Path(args.target_dir).resolve()

    tag_list: list[str] | None = None
    if args.tags:
        tag_list = [t.strip() for t in args.tags.split(",")]

    result = import_learnings(
        source,
        target,
        min_impact=args.min_impact,
        tags=tag_list,
        dry_run=args.dry_run,
    )

    if result.get("status") == "failed":
        print(f"  ERROR: {result.get('error', 'unknown')}")
        sys.exit(1)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"  {prefix}Source: {result.get('source_project', 'unknown')} ({result.get('total_source', 0)} entries)")
    print(f"  {prefix}Imported: {result.get('imported', 0)}")
    print(f"  {prefix}Skipped (duplicate): {result.get('skipped_duplicate', 0)}")
    print(f"  {prefix}Skipped (filter): {result.get('skipped_filter', 0)}")

    sys.exit(0)


def main() -> None:
    """Entry point for the trw-mcp CLI command."""
    parser = argparse.ArgumentParser(
        prog="trw-mcp",
        description="TRW Framework MCP Server",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to .trw/logs/ and stderr",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run MCP server (default)")

    # init-project
    init_parser = subparsers.add_parser(
        "init-project", help="Bootstrap TRW in a project directory"
    )
    init_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )

    # update-project
    update_parser = subparsers.add_parser(
        "update-project",
        help="Update TRW framework files (preserves user config)",
    )
    update_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    update_parser.add_argument(
        "--pip-install",
        action="store_true",
        help="Also reinstall the trw-mcp Python package",
    )

    # audit
    audit_parser = subparsers.add_parser(
        "audit",
        help="Run comprehensive TRW health audit on a project",
    )
    audit_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    audit_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    audit_parser.add_argument(
        "--output",
        help="Write output to file instead of stdout",
    )
    audit_parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-prune duplicates and resync index",
    )

    # export
    export_parser = subparsers.add_parser(
        "export",
        help="Export TRW data (learnings, runs, analytics)",
    )
    export_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    export_parser.add_argument(
        "--scope",
        choices=["learnings", "runs", "analytics", "all"],
        default="all",
        help="Export scope (default: all)",
    )
    export_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json, csv only for learnings)",
    )
    export_parser.add_argument(
        "--output",
        help="Write output to file instead of stdout",
    )
    export_parser.add_argument(
        "--since",
        help="ISO date filter (YYYY-MM-DD)",
    )
    export_parser.add_argument(
        "--min-impact",
        type=float,
        default=0.0,
        help="Minimum impact threshold for learnings",
    )

    # import-learnings
    import_parser = subparsers.add_parser(
        "import-learnings",
        help="Import learnings from an export file",
    )
    import_parser.add_argument(
        "source_file",
        help="Path to exported JSON file",
    )
    import_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    import_parser.add_argument(
        "--min-impact",
        type=float,
        default=0.0,
        help="Minimum impact threshold for import",
    )
    import_parser.add_argument(
        "--tags",
        help="Comma-separated tag filter",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be imported without writing",
    )

    args = parser.parse_args()

    if args.command == "init-project":
        _run_init_project(args)
        return

    if args.command == "update-project":
        _run_update_project(args)
        return

    if args.command == "audit":
        _run_audit(args)
        return

    if args.command == "export":
        _run_export(args)
        return

    if args.command == "import-learnings":
        _run_import_learnings(args)
        return

    # Default: run MCP server (no subcommand or "serve")
    config = TRWConfig()
    debug = args.debug or config.debug

    _configure_logging(debug=debug, config=config)

    structlog.get_logger().info(
        "trw_server_initialized",
        tools_registered=True,
        debug_mode=debug,
    )

    mcp.run()


if __name__ == "__main__":
    main()
