"""``trw-mcp run adopt`` (PRD-CORE-300-FR08, slice S6b).

The former standalone run-adoption tool was an MCP tool. Adoption is a rare operator action (resuming
a run another session started, or reclaiming one whose owner went away), so it
is a CLI verb that runs the same implementation (``_ceremony_adopt_run.adopt_run``)
with the same containment / terminal-status / live-owner guards.

The MCP tool resolved its pin key from the calling session's own
:class:`~fastmcp.Context` (``resolve_pin_key(ctx, explicit=None)``). A CLI
process has no MCP connection, so there is no ``ctx`` to probe: falling back to
the resolver's lower layers (``TRW_SESSION_ID`` env, the process UUID) would pin
the run to the CLI's own throwaway process rather than to the session the
operator actually means to resume — the same "who does this actually apply to"
hazard PRD-CORE-300 hit with ``delivery recover --new-pid``. ``run adopt``
therefore requires ``--session-id`` explicitly and refuses, before touching the
pin store, when it is not resolvable.

Output is one JSON document with ``--json``, otherwise ``key: value`` lines.
The exit status is 1 when the adoption could not be attempted (``result`` is
``error``) and 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = ["add_run_subcommands", "run_run"]


def add_run_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``run adopt``."""
    run = subparsers.add_parser("run", help="Run lifecycle maintenance verbs (PRD-CORE-141)")
    verbs = run.add_subparsers(dest="run_command")
    adopt = verbs.add_parser("adopt", help="Transfer an existing run's pin to a named session; state-changing")
    adopt.add_argument("--run-path", required=True, help="Absolute path to the run directory (under project root)")
    adopt.add_argument(
        "--session-id",
        required=True,
        help="The session the pin transfers TO. Required: a CLI process has no MCP connection of its own.",
    )
    adopt.add_argument(
        "--force",
        action="store_true",
        help="Override a terminal-status or live-owner refusal",
    )
    adopt.add_argument("--json", dest="as_json", action="store_true")


def _adopt(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    session_id = str(args.session_id or "").strip()
    if not session_id:
        return (
            {
                "error": "session_id_unresolvable",
                "reason": "run adopt has no MCP connection to probe; --session-id names the target explicitly",
                "remediation": "pass --session-id <the session that should own this run>",
            },
            True,
        )

    from trw_mcp.exceptions import StateError
    from trw_mcp.tools._ceremony_adopt_run import adopt_run

    try:
        result: dict[str, Any] = dict(adopt_run(None, str(args.run_path), bool(args.force), pin_key=session_id))
    except StateError as exc:
        return {"error": "adoption_refused", "detail": str(exc)}, True
    return result, False


def run_run(args: argparse.Namespace) -> None:
    """Dispatch ``run adopt``."""
    handler = {"adopt": _adopt}.get(str(args.run_command))
    if handler is None:
        print("usage: trw-mcp run {adopt}", file=sys.stderr)
        sys.exit(2)
    document, failed = handler(args)
    if args.as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(1 if failed else 0)
