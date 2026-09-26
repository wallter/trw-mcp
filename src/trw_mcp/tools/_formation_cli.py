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

_OUTCOMES = ("abandoned", "reassigned")
_HEADERS = ("member", "client", "role", "status", "phase", "build", "review", "delivery", "stale")


def add_formation_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``formation`` verbs on the existing parser."""
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

    # Ledger N2/N3: orchestrator slot changes after creation. Authority is the session
    # pin; --run only guards against acting on the wrong run (T29).
    add_parser = verbs.add_parser("add-slot", help="Add pending slots (optionally admitting a candidate to each)")
    add_parser.add_argument("--from", dest="from_file", required=True, help="YAML/JSON member or list of members")
    add_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )
    remove_parser = verbs.add_parser("remove-slot", help="Remove a slot that never joined")
    remove_parser.add_argument("member_id", help="Pending member to remove")
    remove_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )
    admit_parser = verbs.add_parser("admit", help="Admit an announced candidate to a pending slot")
    admit_parser.add_argument("member_id", help="Pending member slot")
    admit_parser.add_argument("candidate_id", help="Handle the candidate got from trw_inbox(action='announce')")
    admit_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )
    # PRD-FIX-149 FR03: an orchestrator's recorded outcome. `delivered` is not offered:
    # that claim needs the member's own evidence (FR11), so only trw_deliver makes it.
    # --reason is required and must be nonempty: it is what distinguishes an
    # orchestrator's abandonment verdict from a member's own completion claim.
    outcome_parser = verbs.add_parser("set-status", help="Record that a member was abandoned or reassigned")
    outcome_parser.add_argument("member_id", help="Member whose outcome to record")
    outcome_parser.add_argument("outcome", help=f"One of: {', '.join(_OUTCOMES)}")
    outcome_parser.add_argument("--reason", required=True, help="Why (stored on the member's note; must be nonempty)")
    outcome_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )

    # Next batch P0: formation pause/resume with per-member ack (PAUSE-RESUME-DESIGN rev 2).
    pause_parser = verbs.add_parser("pause", help="Pause the formation; members ack with trw_inbox(action='ack_pause')")
    pause_parser.add_argument("--reason", required=True, help="Shown to members (first 200 chars)")
    pause_parser.add_argument("--until", default=None, help="Advisory ISO-8601 end; resume is always explicit")
    pause_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )
    resume_parser = verbs.add_parser("resume", help="End the active pause")
    resume_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )

    # Next batch P0: a harness-tailable, body-free wake (WATCH-WAKE-DESIGN rev 3).
    watch_parser = verbs.add_parser("watch", help="Print one body-free line when this member's mail or status changes")
    watch_parser.add_argument("--formation", required=True, help="Formation id")
    watch_parser.add_argument("--member", required=True, help="Your member id")
    watch_parser.add_argument("--pin-key", default=None, help="Defaults to TRW_SESSION_ID")
    watch_parser.add_argument("--interval-seconds", type=float, default=15.0)
    watch_parser.add_argument("--once", action="store_true", help="Print the current line and exit")

    # PRD-CORE-274 FR16: the only way a comms mailbox changes schema version.
    upgrade_parser = verbs.add_parser("comms-upgrade", help="Upgrade this formation's v3 comms mailbox to v4")
    upgrade_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )
    upgrade_parser.add_argument(
        "--ack", dest="acknowledged", action="append", default=[], help="Member whose live process may go dark"
    )
    rollback_parser = verbs.add_parser("comms-rollback", help="Restore the v3 backup if nothing was written since")
    rollback_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )

    merge_parser = verbs.add_parser(
        "merge", help="Owned merge queue; never targets default branch; lead fast-forwards main"
    )
    merge_verbs = merge_parser.add_subparsers(dest="merge_command")
    enqueue_parser = merge_verbs.add_parser(
        "enqueue", help="Orchestrator: approve immutable branch/SHA and build receipt"
    )
    for field in ("branch", "sha", "member_id", "receipt_id"):
        enqueue_parser.add_argument(field)
    merge_verbs.add_parser("list", help="List durable approvals and outcomes without mutation")
    run_one_parser = merge_verbs.add_parser(
        "run-one", help="Merge one item into an un-checked-out integration branch, never default"
    )
    run_one_parser.add_argument("target", help="Un-checked-out integration branch; lead fast-forwards main separately")


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
        elif command in ("comms-upgrade", "comms-rollback"):
            _run_comms_schema(args, command)
        elif command in ("add-slot", "remove-slot", "admit", "set-status"):
            _run_slots(args, command)
        elif command in ("pause", "resume"):
            _run_pause(args, command)
        elif command == "watch":
            from trw_mcp.comms._watch import main as watch_main

            argv = ["--formation", args.formation, "--member", args.member]
            argv += ["--interval-seconds", str(args.interval_seconds)]
            argv += (["--pin-key", args.pin_key] if args.pin_key else []) + (["--once"] if args.once else [])
            sys.exit(watch_main(argv))
        elif command == "merge":
            _run_merge(args)
        else:
            print(
                "usage: trw-mcp formation {init|brief|status|add-slot|remove-slot|admit|set-status|pause|resume|watch|"
                "comms-upgrade|comms-rollback|merge}",
                file=sys.stderr,
            )
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


def _run_merge(args: argparse.Namespace) -> None:
    from trw_mcp.formation import FormationError, load, merge_enqueue, merge_list, merge_run_one
    from trw_mcp.state._paths import resolve_project_root

    action = args.merge_command
    if action not in {"enqueue", "list", "run-one"}:
        raise FormationError("merge requires enqueue, list or run-one")
    run = _orchestrator_run(args) if action == "enqueue" else _resolve_run(args)
    context = load(run)
    if context is None:
        raise FormationError("pinned run belongs to no formation")
    if action == "enqueue":
        item = merge_enqueue(
            context, branch=args.branch, sha=args.sha, member_id=args.member_id, receipt_id=args.receipt_id
        )
        print(json.dumps(item.as_dict(), sort_keys=True))
    elif action == "list":
        print(json.dumps([item.as_dict() for item in merge_list(context)], sort_keys=True))
    else:
        print(
            json.dumps(
                merge_run_one(context, repo=resolve_project_root(), target=args.target).as_dict(), sort_keys=True
            )
        )


def _orchestrator_run(args: argparse.Namespace) -> Path:
    """The run a slot, outcome, or pause change is made AS: this session's pinned run (T29).

    Authority is never a caller-supplied path: any shell can name the
    orchestrator's run directory. The pin (keyed by TRW_SESSION_ID) is what this
    session's own trw_init recorded, so a member session cannot act as the
    orchestrator by passing its path. ``--run`` stays a mis-invocation guard: it
    must name the pinned run. No recency fallback either -- an unpinned session
    has no authority to borrow.
    """
    from trw_mcp.formation import FormationError
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths_pin_mgmt import get_pinned_run

    pinned = get_pinned_run(context=build_call_context(None))
    if pinned is None:
        raise FormationError("no_pinned_run: run trw_init (or trw_session_start) in the orchestrator session first")
    explicit = getattr(args, "run_path", None)
    if explicit and Path(explicit).resolve() != pinned.resolve():
        raise FormationError(f"run_not_pinned: --run {explicit} is not this session's pinned run {pinned}")
    return pinned.resolve()


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


def _run_slots(args: argparse.Namespace, command: str) -> None:
    from trw_mcp.formation import FormationError, add_slots, load, remove_slot, revise

    if command == "set-status":
        if args.outcome not in _OUTCOMES:
            raise FormationError(f"set-status accepts {' or '.join(_OUTCOMES)}, not {args.outcome!r}")
        if not args.reason.strip():
            raise FormationError("set-status requires a nonempty --reason")
    run = _orchestrator_run(args)
    context = load(run)
    if context is None:
        raise FormationError(f"run {run} owns no formation")
    formation_id = context.manifest.formation_id
    if command == "add-slot":
        raw = yaml.safe_load(Path(args.from_file).read_text(encoding="utf-8"))
        members = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(m, dict) for m in members):
            raise FormationError(f"{args.from_file} must hold a member mapping or a list of them")
        manifest = add_slots(formation_id, run, members)
    elif command == "remove-slot":
        manifest = remove_slot(formation_id, run, args.member_id)
    elif command == "set-status":
        manifest = revise(formation_id, run, {args.member_id: {"status": args.outcome, "note": args.reason}})
    else:
        manifest = revise(formation_id, run, {args.member_id: {"admitted_candidate": args.candidate_id}})
    print(f"formation {formation_id} {command} applied at revision {manifest.revision}")


def _run_pause(args: argparse.Namespace, command: str) -> None:
    from trw_mcp.formation import FormationError, load, pause, resume

    run = _orchestrator_run(args)
    context = load(run)
    if context is None:
        raise FormationError(f"run {run} owns no formation")
    formation_id = context.manifest.formation_id
    if command == "pause":
        record = pause(formation_id, run, args.reason, until_utc=args.until)
        print(f"formation {formation_id} paused: pause_id={record.pause_id}")
    else:
        print(f"formation {formation_id} resumed: pause_id={resume(formation_id, run)}")


def _run_brief(args: argparse.Namespace) -> None:
    from trw_mcp.formation import brief

    print(brief(args.member_id, run_path=_resolve_run(args)), end="")


def _run_status(args: argparse.Namespace) -> None:
    from trw_mcp.formation import FormationError, pause_roll_call, status

    board = status(run_path=_resolve_run(args))
    if board is None:
        raise FormationError("no formation is active for this run")
    pause = pause_roll_call(Path(board.manifest_path))
    if getattr(args, "as_json", False):
        from trw_mcp.formation import formation_usage, member_usage

        # PRD-CORE-290-FR01: the usage ledger lives on this CLI surface only (NFR01),
        # and its total sits beside the outcome checks for the same span (NFR02).
        runs = [Path(row.run_path) for row in board.rows if row.run_path]
        members = []
        for row in board.rows:
            usage = member_usage(Path(row.run_path)) if row.run_path else {}
            members.append({**row.as_dict(), **({"usage": usage} if usage else {})})
        outcomes = {
            "members": len(board.rows),
            "builds_passed": sum(row.build == "passed" for row in board.rows),
            "reviews_open": sum(row.review not in ("", "pass", "passed") for row in board.rows),
        }
        print(
            json.dumps(
                {
                    "formation_id": board.formation_id,
                    "revision": board.revision,
                    "manifest_path": board.manifest_path,
                    "members": members,
                    "usage": {**formation_usage(runs), "outcomes": outcomes},
                    "non_terminal": [{"member_id": m, "status": s} for m, s in board.non_terminal],
                    "stalls": [finding.as_dict() for finding in board.stalls],
                    "stall_measurement": board.stall_measurement,
                    "stall_scope": board.stall_scope,
                    **({"pause": pause} if pause is not None else {}),
                },
                indent=2,
            )
        )
        return
    print(f"formation {board.formation_id} (revision {board.revision}) — {board.manifest_path}")
    print(_render_table(board))
    for member_id, member_status in board.non_terminal:
        print(f"  waiting on {member_id}: {member_status}")
    for finding in board.stalls:
        print(f"  {finding.line()}")
    if board.stall_measurement != "measured":
        for source in ("mailbox", "mcp_tool_calls"):
            if board.stall_scope[source] != "measured":
                print(f"  stalls not_measured: {source} unavailable")
    if pause is not None:
        acked, missing = list(pause["acked"]), list(pause["not_acked"])  # type: ignore[call-overload]
        overdue = " OVERDUE" if pause.get("overdue") else ""
        print(f"PAUSED {pause['pause_id']} since {pause['since_utc']}{overdue}: {pause['reason']}")
        print(f"  acked {len(acked)}/{len(acked) + len(missing)}; not acked: {', '.join(missing) or 'none'}")


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


def _run_comms_schema(args: argparse.Namespace, command: str) -> None:
    """FR16 upgrade/rollback. Every refusal exits non-zero with a named reason.

    "Orchestrator-only" is decided from this session's pinned run, never a
    caller-supplied ``--run`` (T29), as for the slot and pause verbs.
    """
    import sqlite3

    from trw_mcp.comms import _upgrade
    from trw_mcp.comms._store import StoreError
    from trw_mcp.formation import FormationError, load
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir

    context = load(_orchestrator_run(args), trw_dir=resolve_trw_dir())
    if context is None:
        raise FormationError("no formation is active for this run")
    if not context.is_orchestrator:
        raise FormationError(f"{command} is orchestrator-only; run it from the orchestrator's pinned session")
    config = get_config()
    try:
        if command == "comms-upgrade":
            result = _upgrade.upgrade(
                context.manifest_path,
                acknowledged=args.acknowledged,
                ttl_seconds=config.comms_message_ttl_seconds,
                busy_timeout_ms=config.comms_sqlite_busy_timeout_ms,
            )
        else:
            result = _upgrade.rollback(context.manifest_path, busy_timeout_ms=config.comms_sqlite_busy_timeout_ms)
    except StoreError as exc:
        print(f"formation: {command} refused: {exc}", file=sys.stderr)
        sys.exit(1)
    except (sqlite3.Error, OSError) as exc:
        print(f"formation: {command} refused: storage_unavailable: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
