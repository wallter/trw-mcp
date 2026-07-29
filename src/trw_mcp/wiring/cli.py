"""FR07 — the enforcing CI entry point.

``python -m trw_mcp.wiring.cli`` exits non-zero on a finding **by default**.
There is no configuration that makes it advisory, no environment variable that
disables it, and the one opt-out (``--advisory``) must be typed by a human on a
command line — FR08's self-check fails the build if it ever appears in the
``Makefile`` recipe.

That asymmetry is the entire point. A prior detector on this repository shipped
as a JSON report, ran exactly once, and 117 days later at least seven of its
findings were still open. A better analyzer emitting a better report reproduces
that outcome exactly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trw_mcp.wiring._manifest import ManifestUnavailableError
from trw_mcp.wiring.baseline import partition
from trw_mcp.wiring.config import DEFAULT_CONFIG
from trw_mcp.wiring.detector import run_detector
from trw_mcp.wiring.model import RegistryError
from trw_mcp.wiring.report import render_report

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INPUT_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trw-wiring-gate",
        description="Observation-based wiring detector (PRD-CORE-232 Phase A). Enforcing by default.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="repository root to scan (default: current working directory)",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help=(
            "print findings but exit 0. Interactive escape hatch ONLY — FR08's self-check fails "
            "the build if this flag appears in the Makefile recipe."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the detector and return the process exit code."""
    args = _build_parser().parse_args(argv)
    repo_root: Path = args.repo_root.resolve()

    try:
        result = run_detector(repo_root, DEFAULT_CONFIG)
    except (ManifestUnavailableError, RegistryError) as exc:
        # A missing or malformed input is a hard failure, never a silent pass:
        # "the check could not run" and "the check found nothing" must never
        # look the same from the outside.
        print(f"wiring detector INPUT ERROR: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    outcome = partition(list(result.findings))
    print(render_report(result, outcome))

    if outcome.is_clean:
        return EXIT_OK
    if args.advisory:
        print(
            "\nADVISORY MODE: findings above are NOT blocking this invocation. "
            "This flag is an interactive escape hatch; it is never valid in CI.",
            file=sys.stderr,
        )
        return EXIT_OK
    return EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
