"""``trw-mcp receipt verify`` — thin adapter over ``record_verification_receipt``.

Experimental software-factory slice 1. Exit 0 when a receipt was written, 1 on a
refusal (nothing written), 2 when the factory is disabled or misconfigured, 3 when the experiment is
overdue (PRD-CORE-340-FR11/FR12; experimental, off by default). Output is one JSON document with ``--json``,
otherwise ``key: value`` lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["add_receipt_subcommands", "run_receipt"]


def add_receipt_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``receipt verify``."""
    receipt = subparsers.add_parser("receipt", help="Evidence receipt writers (experimental software-factory slice 1)")
    verbs = receipt.add_subparsers(dest="receipt_command")
    verify = verbs.add_parser(
        "verify",
        help="Write a content-bound VerificationReceipt for a receiver's intended-use check; state-changing",
        description=(
            "EXPERIMENTAL (Alpha, off by default; enable with factory_enabled).\n"
            "Records that a receiver ran an intended-use check against a commit: it reads the\n"
            "evidence files you name, binds their content to the commit, and writes one\n"
            "VerificationReceipt under the run directory. It never runs the check itself."
        ),
        epilog=(
            "example:\n"
            "  trw-mcp receipt verify --run .trw/runs/task/run1 --subject <40-hex-sha> \\\n"
            "      --check 'pytest tests/test_x.py' --exit-code 0 --evidence reports/x.log\n\n"
            "exit codes: 0 receipt written; 1 refused, nothing written (bad input or unreadable evidence);\n"
            "2 factory disabled or misconfigured; 3 experiment overdue."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verify.add_argument("--run", required=True, help="The run directory the receipt is written under")
    verify.add_argument("--subject", required=True, help="The 40-hex commit sha that was checked")
    verify.add_argument("--check", required=True, help="What was checked (bounded free text)")
    verify.add_argument("--exit-code", required=True, type=int, help="The check's actual exit code; 0 means passed")
    verify.add_argument("--evidence", action="append", default=[], help="Evidence file under the project (repeatable)")
    verify.add_argument("--note", default="", help="Optional limitation or note")
    verify.add_argument("--json", dest="as_json", action="store_true")


def run_receipt(args: argparse.Namespace) -> None:
    """Dispatch ``receipt verify``."""
    if str(getattr(args, "receipt_command", "")) != "verify":
        print("usage: trw-mcp receipt verify --run RUN --subject SHA --check TEXT --exit-code N", file=sys.stderr)
        sys.exit(2)
    from trw_mcp.state._factory_experiment import exit_code_for
    from trw_mcp.tools._evidence_receipts import VerificationReceiptRefusedError, record_verification_receipt

    try:
        result = record_verification_receipt(
            Path(str(args.run)),
            subject=str(args.subject),
            check=str(args.check),
            exit_code=int(args.exit_code),
            evidence_paths=[str(p) for p in args.evidence],
            note=str(args.note),
        )
    except VerificationReceiptRefusedError as exc:
        document: dict[str, object] = {"error": exc.reason_code, "detail": exc.detail}
        exit_code = exit_code_for(exc.reason_code)
    else:
        document = {
            "receipt_id": result.receipt_id,
            "typed_receipt_state": result.typed_receipt_state,
            "passed": result.passed,
            "git_sha": result.git_sha,
            "path": str(result.path),
            "validation": result.validation,
        }
        exit_code = 0
    if args.as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(exit_code)
