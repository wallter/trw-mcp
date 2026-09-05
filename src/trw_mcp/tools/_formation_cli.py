"""``trw-mcp formation ...`` — the client-neutral half of the surface (FR03/06/07).

WHY A CLI AND NOT A TOOL. A tool DEFINITION — description plus parameter JSON
Schema — is paid in the system prompt of every session of every client that
loads the surface, whether or not it is ever called. Three formation verbs would
be three permanent taxes for a surface most sessions never touch. The CLI costs
nothing until it is run, and it is reachable from every one of the seven
supported client profiles, including those that cannot call MCP tools at all
(NFR05). The registered MCP tool set is unchanged by this PRD, and a set-equality
test asserts it.

This module is an ADAPTER: it parses arguments, calls the facade, and prints.
It never parses the manifest and never imports a private formation module.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:  # import-time cost is paid only by the type checker
    from trw_mcp.formation import FormationStatus

__all__ = ["add_formation_subcommands", "run_formation"]

_HEADERS = ("member", "client", "role", "status", "phase", "build", "review", "delivery", "stale")


def add_formation_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``formation init|brief|status`` on the existing parser."""
    parser = subparsers.add_parser("formation", help="Create, brief, and report on a formation (PRD-CORE-265)")
    verbs = parser.add_subparsers(dest="formation_command")

    init_parser = verbs.add_parser("init", help="Write a formation manifest under an orchestrator run")
    init_parser.add_argument("--from", dest="from_file", required=True, help="YAML/JSON file holding the payload")
    init_parser.add_argument("--run", dest="run_path", default=None, help="Orchestrator run directory")

    brief_parser = verbs.add_parser("brief", help="Render a member's brief from the manifest")
    brief_parser.add_argument("member_id", help="Member to brief")
    brief_parser.add_argument("--run", dest="run_path", default=None, help="A run inside the formation")

    status_parser = verbs.add_parser("status", help="Read-only member roll-up")
    status_parser.add_argument("--run", dest="run_path", default=None, help="A run inside the formation")
    status_parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON instead of a table")


def run_formation(args: argparse.Namespace) -> None:
    """Dispatch one formation verb. Exits non-zero on every refusal."""
    from trw_mcp.formation import FormationError

    command = getattr(args, "formation_command", None)
    try:
        if command == "init":
            _run_init(args)
        elif command == "brief":
            _run_brief(args)
        elif command == "status":
            _run_status(args)
        else:
            print("usage: trw-mcp formation {init|brief|status}", file=sys.stderr)
            sys.exit(2)
    except FormationError as exc:
        print(f"formation: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def _resolve_run(args: argparse.Namespace) -> Path:
    """Explicit ``--run`` wins; otherwise the run this session is pinned to."""
    explicit = getattr(args, "run_path", None)
    if explicit:
        return Path(explicit).resolve()
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import resolve_run_path

    # PRD-CORE-141 FR03: a CLI process has no MCP ctx, so the pin key comes from
    # TRW_SESSION_ID (or the process identity) — never a scan of other sessions' runs.
    return resolve_run_path(None, context=build_call_context(None))


def _run_init(args: argparse.Namespace) -> None:
    from trw_mcp.formation import FormationError, create

    payload_path = Path(args.from_file)
    try:
        raw = yaml.safe_load(payload_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FormationError(f"formation payload {payload_path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise FormationError(f"formation payload {payload_path} must contain a mapping")
    manifest = create(_resolve_run(args), raw)
    print(f"formation {manifest.formation_id} created at revision {manifest.revision}")
    print(str(Path(manifest.orchestrator_run_path) / "formation.yaml"))


def _run_brief(args: argparse.Namespace) -> None:
    from trw_mcp.formation import brief

    print(brief(args.member_id, run_path=_resolve_run(args)), end="")


def _run_status(args: argparse.Namespace) -> None:
    from trw_mcp.formation import FormationError, status

    board = status(run_path=_resolve_run(args))
    if board is None:
        raise FormationError("no formation is active for this run")
    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "formation_id": board.formation_id,
                    "revision": board.revision,
                    "manifest_path": board.manifest_path,
                    "members": [row.as_dict() for row in board.rows],
                    "non_terminal": [{"member_id": m, "status": s} for m, s in board.non_terminal],
                },
                indent=2,
            )
        )
        return
    print(f"formation {board.formation_id} (revision {board.revision}) — {board.manifest_path}")
    print(_render_table(board))
    for member_id, member_status in board.non_terminal:
        print(f"  waiting on {member_id}: {member_status}")


def _render_table(board: FormationStatus) -> str:
    rows = [
        [
            row.member_id,
            row.client,
            row.role,
            row.status,
            row.phase,
            row.build,
            row.review,
            row.delivery,
            row.stale_reason if row.stale else "",
        ]
        for row in board.rows
    ]
    widths = [
        max(len(_HEADERS[i]), *(len(r[i]) for r in rows)) if rows else len(_HEADERS[i]) for i in range(len(_HEADERS))
    ]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(_HEADERS))]
    lines.append("  ".join("-" * widths[i] for i in range(len(_HEADERS))))
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    return "\n".join(lines)
