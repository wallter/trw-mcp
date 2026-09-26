"""``memory`` CLI subparsers: ``token`` (PRD-CORE-298 FR02), ``migrate`` (PRD-CORE-280 FR03), ``reembed`` (PRD-CORE-302 FR07).

Also ``models fetch`` (PRD-CORE-302 W40), the one explicit model download.

Belongs to the ``_cli_argparse_operational.py`` facade, which calls
:func:`add_memory_subcommands` while registering the operational surface.
"""

from __future__ import annotations

import argparse

__all__ = ["add_memory_subcommands", "add_models_subcommands"]


def add_memory_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``memory token``, ``memory migrate`` and ``memory reembed``."""
    memory_parser = subparsers.add_parser("memory", help="Memory daemon grants and store migration for this checkout")
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    token_parser = memory_sub.add_parser(
        "token", help="Mint a daemon token granted this checkout's project namespace and user:local"
    )
    token_parser.add_argument("--target-dir", default=".", help="Checkout root (default: .)")
    token_parser.add_argument(
        "--namespace",
        default=None,
        help="Grant this pinned namespace even though the checkout's location derives another (a moved checkout)",
    )
    token_parser.add_argument("--grant", action="append", help="Narrow the grant to this owned namespace (repeatable)")
    token_parser.add_argument(
        "--migrate", action="store_true", help="Delete the Slice A all-namespace daemon-token first"
    )
    # memory migrate (PRD-CORE-280 FR03): move the project store into the user store, explicitly
    migrate_parser = memory_sub.add_parser("migrate", help="Move this checkout's project store into the user store")
    migrate_parser.add_argument("--to", choices=["user"], required=True, help="Destination store")
    migrate_parser.add_argument("--target-dir", default=".", help="Checkout root (default: .)")
    migrate_mode = migrate_parser.add_mutually_exclusive_group()
    migrate_mode.add_argument("--apply", action="store_true", help="Move it (default: preview, writing nothing)")
    migrate_mode.add_argument("--rollback", metavar="MANIFEST", help="Restore the project store a migration moved")
    reembed_parser = memory_sub.add_parser(
        "reembed", help="Re-encode this checkout's vectors outside the daemon's active embedding space"
    )
    reembed_parser.add_argument("--target-dir", default=".", help="Checkout root (default: .)")
    reembed_parser.add_argument("--json", dest="as_json", action="store_true", help="Print one JSON document")


def add_models_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``models fetch``."""
    models_parser = subparsers.add_parser("models", help="Download the embedding and re-rank models")
    models_sub = models_parser.add_subparsers(dest="models_command")
    fetch_parser = models_sub.add_parser(
        "fetch", help="Download the daemon's models into the local cache (runtime loads never download)"
    )
    fetch_parser.add_argument("--json", dest="as_json", action="store_true", help="Print one JSON document")
