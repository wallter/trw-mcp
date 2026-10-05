"""``trw-mcp local status`` flags for the v1 status contract (PRD-CORE-354-FR03).

Split out of ``_cli_argparse`` to keep that module under the effective-LOC gate.
"""

from __future__ import annotations

import argparse


def add_local_status_args(parser: argparse.ArgumentParser) -> None:
    """Machine-readable status contract and one-line render flags."""
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print the v1 status snapshot as JSON (same as --format json)",
    )
    parser.add_argument(
        "--format",
        dest="status_format",
        choices=("text", "json", "line"),
        default="text",
        help="Output format: text (default), json (v1 snapshot), line (one-line status bar)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session whose pinned run to report (json/line; default TRW_SESSION_ID, then CLAUDE_CODE_SESSION_ID)",
    )
    parser.add_argument(
        "--cache-ttl",
        type=float,
        default=None,
        help="json/line: reuse .trw/runtime/status/<session>.json when younger than SECONDS (opt-in write)",
    )
