"""``backup`` CLI subparsers: create + restore (PRD-CORE-311 FR05/FR06).

Belongs to the ``_cli_argparse_operational.py`` facade, which calls
:func:`add_backup_subcommands` while registering the operational surface.
"""

from __future__ import annotations

import argparse

__all__ = ["add_backup_subcommands"]


def add_backup_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``backup create`` and ``backup restore``."""
    backup_parser = subparsers.add_parser(
        "backup", help="Off-machine memory-store backup: local archive + presigned-upload restore drill"
    )
    backup_sub = backup_parser.add_subparsers(dest="backup_command")

    create_parser = backup_sub.add_parser(
        "create", help="Create a local gzip archive; uploads it too when backup_remote_enabled is true"
    )
    create_parser.add_argument(
        "--db", default=None, help="Store to archive (default: the store the memory daemon serves)"
    )

    restore_parser = backup_sub.add_parser(
        "restore",
        help="Restore the store from 'latest' remote backup (network) or a local archive path (offline drill)",
    )
    restore_parser.add_argument(
        "--from",
        dest="restore_from",
        required=True,
        metavar="latest|PATH",
        help="'latest' fetches the newest remote backup; any other value is a local .db.gz archive path (no network)",
    )
    restore_parser.add_argument(
        "--db", default=None, help="Store to restore into (default: the store the memory daemon serves)"
    )
    restore_parser.add_argument(
        "--yes", action="store_true", help="Replace the store without asking (it is archived first either way)"
    )
    restore_parser.add_argument(
        "--no-snapshot",
        dest="no_snapshot",
        action="store_true",
        help="With --yes: replace the store WITHOUT keeping a copy first (e.g. the disk is full)",
    )
