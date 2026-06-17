"""CLI argparse builder — extracted from _cli.py for module-size compliance.

Belongs to the ``_cli.py`` facade. Re-exported there for back-compat with
test imports (``test_devex_fix065.py``, ``test_cli_auth_subcommand.py``).

Single helper:
- ``_build_arg_parser`` — configures argparse parser with all subcommands
"""

from __future__ import annotations

import argparse

from trw_mcp import __version__
from trw_mcp.server._cli_argparse_operational import add_operational_subcommands
from trw_mcp.server._cli_argparse_project import add_project_subcommands


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

    # Project-management subcommands (init-project / update-project / audit /
    # export / import-learnings) live in a sibling module to keep this parser
    # builder under the 350 effective-LOC module gate (PRD-DIST-243).
    add_project_subcommands(subparsers)

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
    uninstall_parser = subparsers.add_parser("uninstall", help="Remove TRW files from a project")
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
    uninstall_parser.add_argument(
        "--user-tier",
        action="store_true",
        help="Also remove the machine-local ~/.trw user-tier store (PRD-SEC-006)",
    )
    uninstall_parser.add_argument(
        "--keep-memory",
        action="store_true",
        help="Preserve the learning corpus (.trw/memory + .trw/learnings) while removing all other TRW state",
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

    # doctor (PRD-QUAL-106) — first-run read-only diagnostic
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run read-only first-run diagnostics (env, config, MCP, profile, instruction surfaces, memory)",
    )
    doctor_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    doctor_parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human)",
    )
    doctor_parser.add_argument(
        "--fix",
        action="store_true",
        help="Print suggested remediation (suggest-only in v1 — applies nothing)",
    )

    # local (PRD-FIX-073: offline ceremony fallback)
    local_parser = subparsers.add_parser(
        "local",
        help="Offline ceremony fallback — init/status/learn/deliver without MCP server",
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
    local_status = local_sub.add_parser("status", help="Show active local run status")
    local_status.add_argument(
        "--run-path",
        default=None,
        help="Explicit run directory path (auto-detects if omitted)",
    )
    local_learn = local_sub.add_parser("learn", help="Persist a learning without MCP transport")
    local_learn.add_argument("--summary", required=True, help="One-line learning summary")
    local_learn.add_argument("--detail", required=True, help="Learning detail")
    local_learn.add_argument("--tag", action="append", default=[], help="Learning tag; may be passed more than once")
    local_deliver = local_sub.add_parser("deliver", help="Mark the active local run delivered")
    local_deliver.add_argument("--message", "-m", default="local delivery", help="Delivery checkpoint message")
    local_deliver.add_argument(
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

    # Operational subcommands (build-release / channel-doctor / session-changelog
    # / tendencies / version-status / tier) live in a sibling module to keep this
    # parser builder under the 350 effective-LOC module gate (PRD-DIST-243).
    add_operational_subcommands(subparsers)

    return parser
