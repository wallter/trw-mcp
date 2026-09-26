"""``trw-mcp prd create|diff`` (PRD-CORE-300-FR07, slice S5).

PRD create and PRD diff were two MCP tools paid in every
session's prompt for a rare, deliberate authoring action (create) and an
occasional review action (diff). They become ``trw-mcp prd create`` and
``trw-mcp prd diff``: same implementations (``create_prd`` in
``requirements.py``, ``prd_diff_report`` in ``query_tools.py``), same
arguments as flags. ``trw_prd_validate`` is unaffected — it stays a tool,
and the ``trw-prd-ready`` skill is the front door: create via the CLI, then
validate via the tool, in the same run.

Only ``create`` is state-changing (it writes a PRD file); ``diff`` is
read-only and runs even under the reviewer role or a dispatched child.

Output is one JSON document with ``--json``, otherwise ``key: value``
lines (list/dict values are rendered as compact JSON so a non-JSON run
still gets one line per field).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = ["add_prd_create_diff_subcommands", "run_prd"]


def add_prd_create_diff_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``prd create`` and ``prd diff`` next to ``prd-state``/``prd-epoch``."""
    prd = subparsers.add_parser("prd", help="Generate or diff an AARE-F PRD (PRD-CORE-300-FR07)")
    prd_verbs = prd.add_subparsers(dest="prd_command")

    create = prd_verbs.add_parser("create", help="Generate a PRD from a feature description and write it to disk")
    create.add_argument("--input-text", required=True, help="Feature request/description; becomes Problem Statement")
    create.add_argument("--category", default="CORE", help="CORE|QUAL|INFRA|LOCAL|EXPLR|RESEARCH|FIX (extendable)")
    create.add_argument("--priority", default="P1", help="P0|P1|P2|P3")
    create.add_argument("--title", default="", help="Defaults to the first line of --input-text")
    create.add_argument("--sequence", type=int, default=1, help="Auto-increments from 1 when left at the default")
    create.add_argument("--risk-level", default="", help="critical|high|medium|low")
    create.add_argument(
        "--verification-mappings",
        default=None,
        help="JSON array of verification-mapping objects",
    )

    diff = prd_verbs.add_parser("diff", help="Diff two PRD files' requirements, metrics, and acceptance gates")
    diff.add_argument("--before-path", required=True)
    diff.add_argument("--after-path", required=True)

    for parser in (create, diff):
        parser.add_argument("--json", dest="as_json", action="store_true")


def _emit(document: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            rendered = json.dumps(value, default=str) if isinstance(value, (dict, list)) else value
            print(f"{key}: {rendered}")
    sys.exit(0)


def _create(args: argparse.Namespace) -> dict[str, Any]:
    from trw_mcp.exceptions import ValidationError
    from trw_mcp.tools.requirements import create_prd

    verification_mappings = json.loads(args.verification_mappings) if args.verification_mappings else None
    try:
        result = create_prd(
            input_text=args.input_text,
            category=args.category,
            priority=args.priority,
            title=args.title,
            sequence=args.sequence,
            risk_level=args.risk_level,
            verification_mappings=verification_mappings,
        )
    except ValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return dict(result)


def _diff(args: argparse.Namespace) -> dict[str, Any]:
    from trw_mcp.tools.query_tools import prd_diff_report

    try:
        return prd_diff_report(before_path=args.before_path, after_path=args.after_path)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def run_prd(args: argparse.Namespace) -> None:
    """Dispatch ``prd create|diff``."""
    handler = {"create": _create, "diff": _diff}.get(str(args.prd_command))
    if handler is None:
        print("usage: trw-mcp prd {create|diff}", file=sys.stderr)
        sys.exit(2)
    document = handler(args)
    _emit(document, as_json=args.as_json)
