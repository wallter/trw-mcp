"""CLI argparse builder — extracted from _cli.py for module-size compliance.

Belongs to the ``_cli.py`` facade. Re-exported there for back-compat with
test imports (``test_devex_fix065.py``, ``test_cli_auth_subcommand.py``).

Single helper:
- ``_build_arg_parser`` — configures argparse parser with all subcommands
"""

from __future__ import annotations

import argparse

from trw_mcp import __version__


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
    parser.add_argument(
        "--allow-unsigned",
        action="store_true",
        default=None,
        help="Allow MCP peers absent from the signed registry; emits mandatory audit events",
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
        choices=["claude-code", "cursor-ide", "cursor-cli", "opencode", "codex", "copilot", "gemini", "aider", "all"],
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
        choices=["claude-code", "cursor-ide", "cursor-cli", "opencode", "codex", "copilot", "gemini", "aider", "all"],
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

    # check-instructions (PRD-CORE-135-FR02)
    check_instr_parser = subparsers.add_parser(
        "check-instructions",
        help="Validate instruction files reference only exposed tools",
    )
    check_instr_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )

    # local (PRD-FIX-073: offline ceremony fallback)
    local_parser = subparsers.add_parser(
        "local",
        help="Offline ceremony fallback — init runs and write checkpoints without MCP server",
    )
    local_sub = local_parser.add_subparsers(dest="local_command")
    local_init = local_sub.add_parser("init", help="Create a run directory")
    local_init.add_argument(
        "--task",
        required=True,
        help="Task name for the run",
    )
    local_cp = local_sub.add_parser("checkpoint", help="Save progress checkpoint")
    local_cp.add_argument(
        "--message",
        "-m",
        default="",
        help="Checkpoint message describing progress",
    )
    local_cp.add_argument(
        "--run-path",
        default=None,
        help="Explicit run directory path (auto-detects if omitted)",
    )

    # gc (PRD-CORE-141 FR11) — stale-run sweep CLI
    gc_parser = subparsers.add_parser(
        "gc",
        help="Sweep stale active runs (mark status=abandoned). TRW_SESSION_ID is inherited from the parent env.",
    )
    # Default is DRY-RUN because this is the safer default for an operator
    # invoking GC manually — the --no-dry-run flag must be explicit to mutate.
    gc_parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Report what would be abandoned without writing (default).",
    )
    gc_parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually mark stale runs as abandoned (the mutating mode).",
    )
    gc_parser.add_argument(
        "--staleness-hours",
        type=int,
        default=None,
        help="Override config.run_staleness_hours for this invocation.",
    )
    gc_parser.add_argument(
        "--grace-hours",
        type=int,
        default=None,
        help="Override config.run_staleness_grace_hours for this invocation.",
    )
    gc_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the StaleRunReport as JSON instead of a human summary.",
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
