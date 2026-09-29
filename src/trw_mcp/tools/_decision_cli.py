"""``trw-mcp decision resolve`` (PRD-CORE-329 FR04/FR08, Slice A).

The resolution path for a decision recorded by ``trw_checkpoint
(blocked_decision=...)``. Follows the existing ``_<noun>_cli.py`` + lazy-verb
pattern ``run adopt``/``delivery recover`` already use.

FR08 layer 1 (advisory, not the enforcing control — see the module docstring
in ``trw_mcp.state._decision_queue`` and PRD-CORE-329 §FR08 for the enforcing
Slice-B integrity check): this verb refuses, before touching the queue, when
``TRW_LOOP_WORKER=1``, ``TRW_DISPATCH_CHILD`` (present, any value), or
``TRW_SURFACE_ROLE=reviewer`` is set in THIS process's own environment. An
honest unattended caller is stopped; a caller that has gone out of its way to
strip the marker before shelling out is not — that residual risk is recorded
in the PRD, not silently closed here.

Output is one JSON document with ``--json``, otherwise ``key: value`` lines,
matching the other CLI-replacement verbs. Exit status: 2 for a malformed
invocation (no ``resolve`` subcommand), 1 for a refused/failed resolution
(unattended lane, unknown id, unreadable queue), 0 on success (including the
idempotent already-resolved case).
"""

from __future__ import annotations

import argparse
import json
import sys

__all__ = ["add_decision_subcommands", "run_decision"]


def add_decision_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``decision resolve``."""
    decision = subparsers.add_parser("decision", help="Blocked-decision queue verbs (PRD-CORE-329)")
    verbs = decision.add_subparsers(dest="decision_command")
    resolve = verbs.add_parser(
        "resolve",
        help="Resolve a pending blocked decision; state-changing, refused in unattended lanes (advisory)",
    )
    resolve.add_argument("id", help="The decision id trw_checkpoint/trw_status returned")
    resolve.add_argument("--choice", required=True, help="The chosen option")
    resolve.add_argument("--json", dest="as_json", action="store_true")


def _resolve(args: argparse.Namespace) -> tuple[dict[str, object], bool]:
    from trw_mcp.state._decision_queue import resolve_decision, unattended_actor_active
    from trw_mcp.state._paths import resolve_trw_dir

    if unattended_actor_active():
        return (
            {
                "error": "resolve_refused_unattended",
                "reason": "TRW_LOOP_WORKER, TRW_DISPATCH_CHILD, or TRW_SURFACE_ROLE=reviewer is set "
                "in this process's environment — an unattended actor cannot resolve its own "
                "decision (PRD-CORE-329 FR08 layer 1, advisory)",
            },
            True,
        )

    record = resolve_decision(
        resolve_trw_dir(),
        decision_id=str(args.id),
        choice=str(args.choice),
        actor="attended",
    )
    if record is None:
        return (
            {
                "error": "unknown_decision_id",
                "id": str(args.id),
                "reason": "no pending record matches this id in decisions.jsonl",
            },
            True,
        )
    return dict(record), False


def run_decision(args: argparse.Namespace) -> None:
    """Dispatch ``decision resolve``."""
    if args.decision_command != "resolve":
        print("usage: trw-mcp decision {resolve}", file=sys.stderr)
        sys.exit(2)
    document, failed = _resolve(args)
    if args.as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(1 if failed else 0)
