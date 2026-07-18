"""Project-management CLI subparser registration helpers.

Belongs to the ``_cli_argparse.py`` parser builder. Extracted to keep that
builder under the 350 effective-LOC module gate (PRD-DIST-243).
"""

from __future__ import annotations

import argparse

__all__ = ["add_project_subcommands"]

_IDE_CHOICES = ["claude-code", "cursor-ide", "cursor-cli", "opencode", "codex", "copilot", "antigravity-cli", "all"]

# Retired client identifiers (2026-07-11): recognized at the CLI so ``--ide
# gemini`` reports a 'retired' message with a migration hint instead of a
# generic "invalid choice" (retired != unknown). Google deprecated the Gemini
# CLI; aider never had a TRW adapter.
_RETIRED_IDE_HINTS: dict[str, str] = {
    "gemini": (
        "Gemini CLI was deprecated by Google — configure antigravity-cli instead "
        "(--ide antigravity-cli). Existing .gemini/ files are left in place; run "
        "'trw-mcp uninstall' to remove them on demand."
    ),
    "aider": "aider never had a TRW client adapter.",
}


def _ide_choice(value: str) -> str:
    """argparse ``type`` for ``--ide`` that distinguishes retired from unknown.

    A retired id raises with a 'retired' message + migration hint (before
    argparse's ``choices`` check fires). Any other value is returned unchanged
    and validated against ``_IDE_CHOICES`` by argparse ("invalid choice" for a
    genuinely unknown id).
    """
    if value in _RETIRED_IDE_HINTS:
        raise argparse.ArgumentTypeError(f"'{value}' support has been retired. {_RETIRED_IDE_HINTS[value]}")
    return value


def add_project_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register init/update/audit/export/import-learnings subcommands."""
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
        choices=_IDE_CHOICES,
        type=_ide_choice,
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
        choices=_IDE_CHOICES,
        type=_ide_choice,
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
