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

    # channel-doctor (PRD-DIST-2400 FR18)
    cd_parser = subparsers.add_parser(
        "channel-doctor",
        help="Channel manifest hygiene: validate, init, scan locks, clean stale",
    )
    cd_parser.add_argument(
        "--project-dir",
        dest="project_dir",
        default=".",
        help="Project root directory (default: current directory)",
    )
    cd_sub = cd_parser.add_subparsers(dest="channel_doctor_command")

    cd_sub.add_parser(
        "init",
        help="Create .trw/channels/ directory and empty manifest if absent",
    )

    cd_sub.add_parser(
        "validate",
        help="Validate .trw/channels/manifest.yaml schema (exits 1 on error)",
    )

    cd_scan = cd_sub.add_parser(
        "scan",
        help="Scan for orphaned locks and stale state files",
    )
    cd_scan.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        dest="max_age_hours",
        help="Age threshold in hours for orphaned locks (default: 24)",
    )
    cd_scan.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        dest="dry_run",
        help="Report without removing (default: True for scan)",
    )

    cd_clean = cd_sub.add_parser(
        "clean",
        help="Remove orphaned locks older than --max-age-hours",
    )
    cd_clean.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        dest="max_age_hours",
        help="Age threshold in hours for orphaned locks (default: 24)",
    )
    cd_clean.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview removals without deleting",
    )

    # channel-doctor stats (meta-tune consumer)
    cd_stats = cd_sub.add_parser(
        "stats",
        help="Show per-channel correlation + throttle stats (meta-tune consumer)",
    )
    cd_stats.add_argument(
        "--window-hours",
        type=int,
        default=1,
        dest="window_hours",
        help="Correlation time window in hours (default: 1)",
    )
    cd_stats.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Output stats as JSON instead of a human table",
    )

    # channel-doctor throttle (meta-tune consumer)
    cd_throttle = cd_sub.add_parser(
        "throttle",
        help="Evaluate (and optionally apply) throttle decisions for all channels",
    )
    cd_throttle.add_argument(
        "--window-hours",
        type=int,
        default=1,
        dest="window_hours",
        help="Correlation time window in hours (default: 1)",
    )
    cd_throttle.add_argument(
        "--apply",
        action="store_true",
        dest="apply",
        help="Execute tier changes (default: dry-run)",
    )
    cd_throttle.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview throttle decisions without applying (default mode)",
    )

    # session-changelog (PRD-LOCAL-049 FR04) — regenerate/print a run's changelog
    changelog_parser = subparsers.add_parser(
        "session-changelog",
        help="Regenerate or print the session changelog for a run path (read-only unless --write)",
    )
    changelog_parser.add_argument(
        "run_path",
        help="Path to the run directory (the dir containing meta/).",
    )
    changelog_parser.add_argument(
        "--write",
        action="store_true",
        help="Persist the report to <run>/reports/session-changelog.md and print its path.",
    )
    changelog_parser.add_argument(
        "--advisory",
        action="store_true",
        help="Include the package-changelog coverage advisory (FR03).",
    )

    # tendencies (PRD-QUAL-109 FR-03) — advisory AI-development tendency report
    tendencies_parser = subparsers.add_parser(
        "tendencies",
        help="Advisory scan for AI-development tendencies (PRD-count uniformity, stub-closure chains, "
        "benchmark saturation, status-flip-only PRDs). Exit 0 always; never blocks.",
    )
    tendencies_parser.add_argument(
        "--corpus",
        default=None,
        help="Corpus root to scan (default: .trw/distill/handoff-archive + the PRD catalogue when present).",
    )
    tendencies_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit findings as JSON for CI/telemetry ingestion instead of a human report.",
    )

    # version-status
    version_parser = subparsers.add_parser(
        "version-status",
        help="Print authoritative package/framework/live-server version status.",
    )
    version_parser.add_argument(
        "--project-root",
        default=".",
        help="Project root containing package manifests and .trw/frameworks/VERSION.yaml.",
    )
    version_parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when authoritative version surfaces drift.",
    )

    # tier (PRD-DIST-1996, c748): operator entitlement provisioning
    tier_parser = subparsers.add_parser(
        "tier",
        help="Manage TRW tier entitlements (.trw/entitlements.yaml)",
    )
    tier_sub = tier_parser.add_subparsers(dest="tier_command")

    # tier issue
    issue_parser = tier_sub.add_parser(
        "issue",
        help="Generate a signed entitlement YAML",
    )
    issue_parser.add_argument(
        "--tier",
        choices=("free", "team", "pro", "enterprise"),
        required=True,
        help="Tier to issue",
    )
    issue_parser.add_argument(
        "--issued-to",
        required=True,
        help="Operator identifier (email, username, or org name)",
    )
    issue_parser.add_argument(
        "--expires",
        required=True,
        help="Expiry date ISO-8601 (e.g. 2027-01-01 or 2027-01-01T00:00:00+00:00)",
    )
    issue_parser.add_argument(
        "--trw-dir",
        default=".trw",
        help="Target .trw/ directory (default: ./.trw)",
    )
    issue_parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the YAML to stdout instead of writing to disk",
    )

    # tier show
    tier_sub.add_parser(
        "show",
        help="Print resolved tier + status from .trw/entitlements.yaml",
    )
    status_parser = tier_sub.add_parser(
        "status",
        help="Print tier entitlement status as an auditable table",
    )
    status_parser.add_argument(
        "--trw-dir",
        default=".trw",
        help="Target .trw/ directory (default: ./.trw)",
    )

    return parser
