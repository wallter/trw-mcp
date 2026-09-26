"""``trw-mcp delivery recover`` (PRD-CORE-300-FR03).

Delivery recovery was an MCP tool. It is a rare operator action after a crash
or a stale lease, so it is a CLI verb that runs the same implementation with the
same capability, expected-revision and reason checks. It is state-changing, so
the FR02 guard refuses it under the reviewer role or in a dispatched child.

Reading a delivery's status stays on MCP as ``trw_status(delivery=...)``; its
``resume_pid`` is the value ``--new-pid`` needs for ``--action resume``.

Output is one JSON document with ``--json``, otherwise ``key: value`` lines. As
for the other replacement verbs, the exit status is 1 when the action could not
be attempted (``result`` is ``error``, ``invalid_request`` or
``unsupported_action``) and 0 otherwise: a recovery outcome such as
``not_found`` or a refused capability is a result, reported in ``status``.
"""

from __future__ import annotations

import argparse
import json
import sys

__all__ = ["add_delivery_subcommands", "run_delivery"]

_NOT_ATTEMPTED = frozenset({"error", "invalid_request", "unsupported_action"})


def add_delivery_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``delivery recover``."""
    from trw_mcp.tools.delivery_ops import _SUPPORTED_ACTIONS, _TAKEOVER

    delivery = subparsers.add_parser("delivery", help="Recover a delivery operation (PRD-CORE-208)")
    verbs = delivery.add_subparsers(dest="delivery_command")
    recover = verbs.add_parser("recover", help="Recover a stale or crashed delivery; state-changing")
    recover.add_argument("--delivery-id", required=True, help="The delivery_id trw_deliver returned")
    recover.add_argument("--action", default=_TAKEOVER, help=f"One of: {', '.join(_SUPPORTED_ACTIONS)}")
    recover.add_argument("--capability-token", default="", help="The capability_token trw_deliver returned")
    recover.add_argument("--expected-revision", type=int, default=0, help="The revision trw_status(delivery=) shows")
    recover.add_argument("--reason", default="")
    recover.add_argument("--new-owner", default="")
    recover.add_argument("--new-pid", type=int, default=0, help="For resume: the resume_pid trw_status reports")
    recover.add_argument("--effect-id", default="", help="For reconcile_applied / reconcile_not_applied")
    recover.add_argument("--evidence-ref", default="")
    recover.add_argument("--json", dest="as_json", action="store_true")


def run_delivery(args: argparse.Namespace) -> None:
    """Dispatch ``delivery recover``."""
    if args.delivery_command != "recover":
        print("usage: trw-mcp delivery {recover}", file=sys.stderr)
        sys.exit(2)
    from trw_mcp.tools.delivery_ops import delivery_recover

    document = delivery_recover(
        delivery_id=args.delivery_id,
        action=args.action,
        capability_token=args.capability_token,
        expected_revision=args.expected_revision,
        reason=args.reason,
        new_owner=args.new_owner,
        new_pid=args.new_pid,
        effect_id=args.effect_id,
        evidence_ref=args.evidence_ref,
    )
    if args.as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(1 if document.get("result") in _NOT_ATTEMPTED else 0)
