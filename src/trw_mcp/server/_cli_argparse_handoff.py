"""``trw-mcp handoff`` subparsers (PRD-CORE-347-FR05, PRD-CORE-348-FR01).

Belongs to the ``_cli_argparse.py`` facade. Registers the ``handoff`` verb group
(subparser dest ``handoff_command``) for Agent Handoff Records: ``validate``,
``digest`` and ``render`` are read-only; ``seal`` writes only the named file.
"""

from __future__ import annotations

import argparse

__all__ = ["add_handoff_subcommands"]


def add_handoff_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``handoff validate|digest|seal|render``."""
    parser = subparsers.add_parser(
        "handoff",
        help="Validate, digest, seal or render an Agent Handoff Record (AHR 1.0-rc.1)",
    )
    sub = parser.add_subparsers(dest="handoff_command")

    validate = sub.add_parser(
        "validate",
        help="Check one AHR file at L1; prints findings as JSON lines, exit 1 on any finding",
    )
    validate.add_argument("file", help="AHR JSON file (handoff, readback or event)")
    validate.add_argument("--handoff", default=None, help="The handoff a readback binds to")

    digest = sub.add_parser("digest", help="Print the RFC 8785 sha256 digest of an AHR file")
    digest.add_argument("file", help="AHR JSON file")

    seal = sub.add_parser(
        "seal",
        help="Write integrity.digest into an AHR file; refuses a record that fails validation",
    )
    seal.add_argument("file", help="AHR JSON file to seal in place")
    seal.add_argument("--handoff", default=None, help="The handoff a readback binds to")

    render = sub.add_parser(
        "render",
        help="Print the Markdown view of a valid handoff record (the JSON stays normative)",
    )
    render.add_argument("file", help="AHR handoff JSON file")
