"""``trw-mcp factory status`` — thin CLI adapter over the packaged factory reader (PRD-CORE-340-FR10).

Experimental (Alpha) and off by default: the reader gates itself through
``state._factory_experiment.check`` (FR11/FR12), so this module only parses arguments.
The legacy ``scripts/factory_status.py`` shares :func:`add_status_arguments` and :func:`run_status_args`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["add_factory_subcommands", "add_status_arguments", "run_factory", "run_status_args"]


def add_status_arguments(parser: argparse.ArgumentParser) -> None:
    """The one definition of the status arguments (CLI and script adapter)."""
    parser.add_argument("--run", action="append", required=True, type=Path, help="run directory (repeatable)")
    parser.add_argument("--now", help="ISO time used for READY age (default: current UTC); never moves the expiry")
    parser.add_argument("--json", dest="as_json", action="store_true")


def run_status_args(args: argparse.Namespace) -> int:
    """Run the reader for parsed status arguments and return its exit code."""
    from trw_mcp.state._factory_status import run_status

    return run_status(list(args.run), args.now, bool(args.as_json))


def add_factory_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``factory status``."""
    factory = subparsers.add_parser("factory", help="Experimental (Alpha) software-factory reader; off by default")
    verbs = factory.add_subparsers(dest="factory_command")
    status = verbs.add_parser(
        "status",
        help="Read-only START/READY/USED report for explicit runs",
        description=(
            "EXPERIMENTAL (Alpha, off by default; enable with factory_enabled).\n"
            "Reads the run directories you name and reports each attempt's START, READY and\n"
            "USED checkpoints with READY age. Read-only and descriptive: it ranks nothing and\n"
            "grants no acceptance."
        ),
        epilog=(
            "example:\n"
            "  trw-mcp factory status --run .trw/runs/task/run1 --run .trw/runs/task/run2 --json\n\n"
            "exit codes: 0 report printed, no diagnostics; 1 report printed with diagnostics for one or more runs;\n"
            "2 factory disabled or misconfigured (or --now is not a timezone-aware ISO time); 3 experiment overdue."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_status_arguments(status)


def run_factory(args: argparse.Namespace) -> None:
    """Dispatch ``factory status``."""
    if str(getattr(args, "factory_command", "")) != "status":
        print("usage: trw-mcp factory status --run RUN [--run RUN] [--now ISO] [--json]", file=sys.stderr)
        sys.exit(2)
    sys.exit(run_status_args(args))
