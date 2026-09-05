"""PRD scheduling-ledger CLI subparsers (``prd-state`` / ``prd-epoch``).

Belongs to the ``_cli_argparse_operational.py`` facade, which calls
:func:`add_prd_subcommands` while registering the operational surface. The
two commands are one unit: ``prd-epoch`` exists solely to make ``prd-state``
activation reachable after PRD-CORE-244-FR07 made it fail closed on an
unevaluated epoch, so they are registered together or not at all.
"""

from __future__ import annotations

import argparse

__all__ = ["add_prd_subcommands"]


def add_prd_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the PRD scheduling-ledger subcommands."""
    # prd-state (PRD-QUAL-121-FR04 production caller)
    prd_state_parser = subparsers.add_parser(
        "prd-state",
        help="Transition a PRD's execution state through the WIP-limited scheduling ledger",
    )
    prd_state_parser.add_argument("--prd-id", required=True, help="PRD identifier (PRD-XXX-NNN)")
    prd_state_parser.add_argument(
        "--state",
        required=True,
        help="Target execution state (candidate|queued|active|blocked_external|...)",
    )
    prd_state_parser.add_argument(
        "--receipt",
        required=True,
        help="Authorization receipt for the scheduling action (required, non-empty)",
    )
    prd_state_parser.add_argument("--actor", required=True, help="Acting identity")
    prd_state_parser.add_argument("--owner", default="", help="Owner consuming the WIP slot")
    prd_state_parser.add_argument("--project-root", default=".", help="Project root (default: current directory)")
    prd_state_parser.add_argument(
        "--prds-dir",
        default="docs/requirements-aare-f/prds",
        help="PRD directory relative to the project root",
    )

    # prd-epoch (PRD-CORE-244-FR07): the operator exit from ``epoch_unset``.
    prd_epoch_parser = subparsers.add_parser(
        "prd-epoch",
        help="Advance the registry evaluation epoch so expiry is evaluated and activation is reachable",
    )
    prd_epoch_parser.add_argument(
        "--receipt",
        required=True,
        help="Authorization receipt for the scheduling action (required, non-empty)",
    )
    prd_epoch_parser.add_argument("--actor", required=True, help="Acting identity")
    prd_epoch_parser.add_argument("--project-root", default=".", help="Project root (default: current directory)")
    prd_epoch_parser.add_argument(
        "--prds-dir",
        default="docs/requirements-aare-f/prds",
        help="PRD directory relative to the project root",
    )
