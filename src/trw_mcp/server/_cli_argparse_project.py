"""Project-management CLI subparser registration helpers.

Belongs to the ``_cli_argparse.py`` parser builder. Extracted to keep that
builder under the 350 effective-LOC module gate (PRD-DIST-243).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trw_mcp.bootstrap._utils import SUPPORTED_IDES

__all__ = ["add_project_subcommands"]

#: DERIVED from the canonical client set, not hand-copied. This was a literal list
#: of the seven ids plus "all". It happened to be in sync, which is the point:
#: a module-local copy of a closed set is correct the day it is written and wrong
#: the moment the set grows, and the person adding a client has no reason to look
#: in the argparse builder. Two other instances of the same shape were found and
#: fixed in the same sweep — the agent-contract linter's client-tree list and the
#: config-key exemption map — so this is a pattern, not an incident.
#:
#: Sorted so `--help` output is stable; "all" is appended because it is a CLI
#: affordance, not a client, and must not leak into the canonical set.
_IDE_CHOICES = [*sorted(SUPPORTED_IDES), "all"]


def _init_target(value: str) -> str:
    """argparse ``type`` for init-project's target: a client id that is not an existing path meant ``--ide``.

    INC-121 (h): ``init-project claude-code`` silently created ``./claude-code/`` and installed there.
    """
    if value in SUPPORTED_IDES and not Path(value).exists():
        raise argparse.ArgumentTypeError(
            f"{value!r} is a client id, not an existing directory: did you mean --ide {value}? "
            "(the positional argument is the target directory; pass . for the current one)"
        )
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
        type=_init_target,
        help="Target project directory (default: current directory)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    init_parser.add_argument(
        "--ide",
        choices=_IDE_CHOICES,
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
        default=None,
        help="Target client; all means recorded clients (every supported client when none recorded)",
    )

    update_parser.add_argument(
        "--reprovision",
        action="append",
        metavar="PATH",
        help="Clear PATH's deletion tombstone so it is written again (repeatable); 'all' clears every tombstone",
    )

    update_parser.add_argument(
        "--rerender",
        action="append",
        metavar="PATH",
        help="Render only named TRW-managed files (repeatable); backs up old bytes under .trw/trash",
    )

    trust_parser = subparsers.add_parser(
        "trust-codex-hooks",
        help="Pre-approve TRW's own Codex hooks in ~/.codex/config.toml, pinned to each hook's current hash",
    )
    trust_parser.add_argument("target_dir", nargs="?", default=".", help="Project directory (default: .)")
    trust_parser.add_argument("--revoke", action="store_true", help="Remove those approvals instead")

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
        help=(
            "Import learnings from a JSON export (not CSV). Every learning gets a NEW id and source_type=agent; the "
            "exported id and source_type are kept as imported-id:/imported-source: tags, and created/updated are "
            "re-stamped at import time."
        ),
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
