"""CLI entry point and argument parser for trw-mcp.

Provides ``main()`` which is the ``trw-mcp`` console_script entry point,
the argument parser builder, and the ``_check_mcp_json_portability`` helper.
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path

import structlog

from trw_mcp import __version__
from trw_mcp._logging import configure_logging
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS
from trw_mcp.server._transport import resolve_and_run_transport


def _check_mcp_json_portability(cwd: Path | None = None) -> None:
    """Warn if ``.mcp.json`` contains a stale absolute path for the trw server.

    Reads ``.mcp.json`` from *cwd* (or ``Path.cwd()``) and checks whether the
    ``mcpServers.trw.command`` value is an absolute path that no longer exists
    on disk.  Logs a warning with remediation instructions if so.

    Does NOT log full file contents (security: may contain API keys for
    other servers).

    Args:
        cwd: Directory to look for ``.mcp.json``.  Defaults to current
            working directory.  Accepts an explicit path for testability.
    """
    import json as _json

    target = cwd or Path.cwd()
    mcp_path = target / ".mcp.json"
    if not mcp_path.exists():
        return

    try:
        data = _json.loads(mcp_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return  # malformed or unreadable -- not our problem here

    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return
    trw_entry = servers.get("trw")
    if not isinstance(trw_entry, dict):
        return

    cmd = str(trw_entry.get("command", ""))
    if cmd.startswith("/") and not Path(cmd).exists():
        log = structlog.get_logger(__name__)
        log.warning(
            "stale_mcp_json_path",
            command=cmd,
            fix="run 'trw-mcp update-project .' to fix",
        )


# ── Argument parser ──────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands.

    Returns:
        Configured ArgumentParser with global flags and subcommand parsers.
    """
    parser = argparse.ArgumentParser(
        prog="trw-mcp",
        description="TRW Framework MCP Server",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to .trw/logs/ and stderr",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-v=DEBUG, -vv=DEBUG+file). Stacks with --debug.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all output except warnings and errors",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Explicit log level (overrides -v/--debug/env vars)",
    )
    parser.add_argument(
        "--log-json",
        action="store_true",
        default=None,
        help="Force JSON log output (default: auto-detect from TTY)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=None,
        help="MCP transport (default: from config or stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address for HTTP transport (default: from config or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for HTTP transport (default: from config or 8100)",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run MCP server (default)")

    # init-project
    init_parser = subparsers.add_parser("init-project", help="Bootstrap TRW in a project directory")
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
    init_parser.add_argument(
        "--source-package",
        default="",
        help="Source package name for build checks (e.g., myapp)",
    )
    init_parser.add_argument(
        "--test-path",
        default="",
        help="Test directory path relative to source (e.g., tests)",
    )
    init_parser.add_argument(
        "--ide",
        choices=["claude-code", "cursor", "opencode", "codex", "copilot", "gemini", "aider", "all"],
        default=None,
        help="Target IDE (auto-detect if not specified)",
    )
    init_parser.add_argument(
        "--runs-root",
        default=".trw/runs",
        help="Directory for run artifacts (default: .trw/runs)",
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
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would change without modifying files",
    )
    update_parser.add_argument(
        "--ide",
        choices=["claude-code", "cursor", "opencode", "codex", "copilot", "gemini", "aider", "all"],
        default=None,
        help="Target IDE (auto-detect if not specified)",
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

    # auth
    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage platform authentication",
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_command")
    login_parser = auth_sub.add_parser("login", help="Authenticate via device authorization flow")
    login_parser.add_argument(
        "--api-url",
        default=None,
        help="Override API URL (default: from config or https://api.trwframework.com)",
    )
    auth_sub.add_parser("logout", help="Remove stored API key")
    auth_sub.add_parser("status", help="Show current authentication status")

    # uninstall
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Remove TRW files from a project",
    )
    uninstall_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Project directory (default: current directory)",
    )
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files without removing them",
    )
    uninstall_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # config-reference
    subparsers.add_parser(
        "config-reference",
        help="Print configuration reference (all TRW_ env vars)",
    )

    # build-release
    build_parser = subparsers.add_parser(
        "build-release",
        help="Build a release bundle (.tar.gz) of bundled data",
    )
    build_parser.add_argument(
        "--version",
        help="Release version (default: read from pyproject.toml)",
    )
    build_parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for the bundle (default: current directory)",
    )
    build_parser.add_argument(
        "--push",
        action="store_true",
        help="Push release to backend after building",
    )
    build_parser.add_argument(
        "--backend-url",
        help="Backend API base URL (required with --push)",
    )
    build_parser.add_argument(
        "--api-key",
        help="API key for backend authentication (required with --push)",
    )

    return parser


def _suggest_command(unknown: str, parser: argparse.ArgumentParser) -> str | None:
    """Return the closest known subcommand to *unknown*, or None if no good match.

    Uses ``difflib.get_close_matches`` with cutoff=0.6 to find typo corrections.
    """
    known: list[str] = []
    for action in parser._subparsers._actions:  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction):
            known.extend(action.choices.keys())
    matches = difflib.get_close_matches(unknown, known, n=1, cutoff=0.5)
    return matches[0] if matches else None


def main() -> None:
    """Entry point for the trw-mcp CLI command.

    Parses arguments, dispatches to subcommand handlers, or starts the
    MCP server with the appropriate transport.
    """
    import logging as _logging
    import sys as _sys

    # Early stderr logging so exceptions during config load are visible.
    # This is replaced by configure_logging() once config is loaded.
    _logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=_logging.DEBUG,
        stream=_sys.stderr,
        force=True,
    )

    parser = _build_arg_parser()
    args = parser.parse_args()

    # Resolve shared CLI logging state before dispatching subcommands so they
    # don't inherit the noisy fallback stdlib logger.
    debug = bool(getattr(args, "debug", False))
    verbosity = int(getattr(args, "verbose", 0))
    if getattr(args, "quiet", False):
        verbosity = -1
    elif debug and verbosity == 0:
        verbosity = 1

    is_subcommand = bool(args.command and args.command != "serve")
    plain_subcommand_output = is_subcommand and not (debug or verbosity > 0 or getattr(args, "log_json", False))
    effective_log_level = getattr(args, "log_level", None)
    if plain_subcommand_output and effective_log_level is None:
        effective_log_level = "WARNING"

    subcommand_log_dir: Path | None = None
    if debug or verbosity >= 2:
        trw_dir = getattr(TRWConfig(), "trw_dir", ".trw")
        logs_dir = getattr(TRWConfig(), "logs_dir", "logs")
        subcommand_log_dir = Path.cwd() / trw_dir / logs_dir

    configure_logging(
        debug=debug,
        verbosity=verbosity,
        log_level=effective_log_level,
        json_output=args.log_json or None,
        log_dir=subcommand_log_dir,
        package_name="trw-mcp",
    )

    # Dispatch subcommands
    cmd = str(args.command or "")
    handler = SUBCOMMAND_HANDLERS.get(cmd)
    if handler is not None:
        handler(args)
        return

    # If unrecognized subcommand (not empty, not "serve"), suggest closest match
    if cmd and cmd != "serve":
        suggestion = _suggest_command(cmd, parser)
        if suggestion:
            print(f"Unknown command '{cmd}'. Did you mean '{suggestion}'?")
        else:
            print(f"Unknown command '{cmd}'. Run 'trw-mcp --help' for available commands.")
        _sys.exit(1)

    # Default: run MCP server (no subcommand or "serve")
    config = TRWConfig()
    debug = args.debug or config.debug

    # Resolve verbosity: --quiet overrides, --debug adds to -v count
    verbosity = args.verbose
    if args.quiet:
        verbosity = -1
    elif debug and verbosity == 0:
        verbosity = 1

    log_dir: Path | None = None
    if debug or verbosity >= 2:
        log_dir = Path.cwd() / config.trw_dir / config.logs_dir

    configure_logging(
        debug=debug,
        verbosity=verbosity,
        log_level=args.log_level,
        json_output=args.log_json or None,
        log_dir=log_dir,
        package_name="trw-mcp",
    )

    # PRD-FIX-037: Warn if .mcp.json has a stale absolute path
    _check_mcp_json_portability()

    log = structlog.get_logger(__name__)
    resolve_and_run_transport(args, config, debug=debug, log=log)
