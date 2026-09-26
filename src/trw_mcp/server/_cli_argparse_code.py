"""``trw-mcp code`` subparsers (PRD-CORE-300-FR06 slice S4).

Belongs to the ``_cli_argparse.py`` facade. Registers the ``code`` verb group
with subparser dest ``code_command`` (the ``f"{verb}_command"`` convention
``_cli_replacements.invoked_command_path`` relies on) and its two commands:
``index`` (replaces the former code-index-build MCP tool, state-changing) and
``risk`` (replaces the former codebase-risk-report MCP tool, read-only) —
see ``server/_cli_replacements.py::CLI_REPLACEMENTS`` for the exact tool
names each command took over from.
"""

from __future__ import annotations

import argparse

__all__ = ["add_code_subcommands"]


def add_code_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``code index`` / ``code risk`` subcommands."""
    code_parser = subparsers.add_parser(
        "code",
        help="Local code-index build and codebase-risk reporting",
    )
    code_sub = code_parser.add_subparsers(dest="code_command")

    index_parser = code_sub.add_parser(
        "index",
        help="Build the local code index that search and symbol lookup read",
    )
    index_parser.add_argument(
        "repo_root",
        nargs="?",
        default=".",
        help="Repository root to index (default: current directory)",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Reclassify all discovered files as freshly added",
    )
    index_parser.add_argument(
        "--paths",
        action="append",
        default=None,
        help="Limit the build to this path (repeatable)",
    )
    index_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the build result as one JSON document instead of a human summary",
    )

    risk_parser = code_sub.add_parser(
        "risk",
        help="Read the ranked file-level composite-risk report for the current SHA",
    )
    risk_parser.add_argument(
        "repo_root",
        nargs="?",
        default=None,
        help="Repository root (default: current directory)",
    )
    risk_parser.add_argument(
        "--cache-dir",
        dest="cache_dir",
        default=None,
        help="Override the sidecar cache directory (default: repo-relative .trw/distill)",
    )
    risk_parser.add_argument(
        "--top-n",
        dest="top_n",
        type=int,
        default=20,
        help="Entries to return; 0 returns all (default: 20)",
    )
    risk_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the risk report as one JSON document instead of a human summary",
    )
