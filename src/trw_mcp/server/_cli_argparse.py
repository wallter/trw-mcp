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
        "--allow-unsigned",
        action="store_true",
        default=None,
        help="Allow MCP peers absent from the signed registry; emits mandatory audit events",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default=None,
        help=argparse.SUPPRESS,
        # Deprecated no-op (2026-07-11): HTTP transport was removed in
        # a0673d9765 and stdio is the only mode, but installer scripts in the
        # wild (install-trw.py <= 0.55.18 bundles, tool overlays) still probe
        # with `--transport stdio serve` while installing the LATEST PyPI
        # trw-mcp. Rejecting the flag turns every such install into a hard
        # fail. Accept exactly "stdio"; any other value still errors.
    )
    parser.add_argument(
        "--memory-db",
        dest="memory_db",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Register an EXTERNAL trw-memory DB as a READ-ONLY source unioned into "
            "trw_recall (PRD-CORE-202). Repeatable; union'd with config.extra_read_stores."
        ),
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
    _API_URL_HELP = "Override API URL (default: from config or https://api.trwframework.com)"
    login_parser = auth_sub.add_parser("login", help="Authenticate via device authorization flow")
    login_parser.add_argument("--api-url", default=None, help=_API_URL_HELP)
    logout_parser = auth_sub.add_parser("logout", help="Remove stored API key")
    logout_parser.add_argument("--api-url", default=None, help=_API_URL_HELP)
    status_parser = auth_sub.add_parser("status", help="Show current authentication status")
    status_parser.add_argument("--api-url", default=None, help=_API_URL_HELP)

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

    # dispatch — run another coding-agent CLI headlessly for a second-opinion audit.
    _add_dispatch_subcommand(subparsers)

    # Operational subcommands (build-release / channel-doctor / session-changelog
    # / tendencies / version-status / tier) live in a sibling module to keep this
    # parser builder under the 350 effective-LOC module gate (PRD-DIST-243).
    add_operational_subcommands(subparsers)

    return parser


def _add_dispatch_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``dispatch`` subcommand (cross-client second-opinion audits)."""
    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help="Run another coding-agent CLI (claude/codex/agy/opencode) headlessly for a second opinion",
    )
    dispatch_parser.add_argument(
        "--client",
        default=None,
        help=(
            "Target CLI: claude | codex | agy | opencode (gemini is EOL — use agy). "
            "Optional: defaults to dispatch.default_client (or a --role default) "
            "from .trw/config.yaml."
        ),
    )
    dispatch_parser.add_argument(
        "--prompt",
        default=None,
        help="The prompt/instruction for the child agent (or use --prompt-file).",
    )
    dispatch_parser.add_argument(
        "--prompt-file",
        dest="prompt_file",
        default=None,
        help="Read the prompt body from a file instead of --prompt.",
    )
    dispatch_parser.add_argument(
        "--role",
        default=None,
        choices=["code-review", "design-audit", "architectural-audit", "adversarial-audit"],
        help="Prepend a read-only second-opinion audit role preamble to the prompt.",
    )
    dispatch_parser.add_argument(
        "--model",
        default=None,
        help="Optional model override for the child client.",
    )
    dispatch_parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for the child process (default: current directory).",
    )
    dispatch_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help=(
            "Hard wall-clock timeout in seconds. Defaults to "
            "dispatch.default_timeout_s from .trw/config.yaml (600 if unset)."
        ),
    )
    dispatch_parser.add_argument(
        "--output-file",
        dest="output_file",
        default=None,
        help="Write the full DispatchResult JSON to this file.",
    )
    dispatch_parser.add_argument(
        "--no-isolate",
        dest="no_isolate",
        action="store_true",
        help="Do NOT isolate the child from host config/hooks/MCP (default: isolate).",
    )
    dispatch_parser.add_argument(
        "--allow-writes",
        dest="allow_writes",
        action="store_true",
        help="Allow the child agent to write/edit (default: read-only).",
    )
    dispatch_parser.add_argument(
        "--pty",
        action="store_true",
        help="Wrap the child in a pseudo-TTY (use if stdout comes back empty, e.g. agy bug #76).",
    )
    dispatch_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full DispatchResult as JSON instead of just the answer text.",
    )
