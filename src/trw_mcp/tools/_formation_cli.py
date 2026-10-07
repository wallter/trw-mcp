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

import yaml

__all__ = ["add_formation_subcommands", "run_formation"]

_OUTCOMES = ("abandoned", "reassigned")


def add_formation_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``formation`` verbs on the existing parser."""
    parser = subparsers.add_parser("formation", help="Create, brief, and report on a formation (PRD-CORE-265)")
    verbs = parser.add_subparsers(dest="formation_command")

    init_parser = verbs.add_parser("init", help="Write a formation manifest under an orchestrator run")
    init_parser.add_argument("--from", dest="from_file", required=True, help="YAML/JSON file holding the payload")
    init_parser.add_argument("--run", dest="run_path", default=None, help="Orchestrator run directory")
    init_parser.add_argument("--orchestrator-member-id", help="Orchestrator member id (default 'orchestrator'; '' off)")

    lead_parser = verbs.add_parser(
        "add-orchestrator", help="Make this orchestrator session an addressable member of its formation"
    )
    lead_parser.add_argument("--member-id", default="orchestrator", help="Id peers use to trw_send to this session")
    lead_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )

    lead_parser.add_argument(
        "--session-id",
        default=None,
        help="Rebind to this session's pin after the lead's MCP pin changed (the pin must own the orchestrator run; "
        "move it with `trw-mcp run adopt --session-id`). Default: this process's own session.",
    )

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
    upgrade_parser = verbs.add_parser("comms-upgrade", help="Upgrade this formation's v3, v4 or v5 comms mailbox to v6")
    upgrade_parser.add_argument(
        "--run", dest="run_path", default=None, help="Must name this session's pinned run (a guard, not authority)"
    )
    upgrade_parser.add_argument(
        "--ack", dest="acknowledged", action="append", default=[], help="Member whose live process may go dark"
    )
    rollback_parser = verbs.add_parser(
        "comms-rollback", help="Restore the pre-upgrade backup if nothing was written since"
    )
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
    from trw_mcp.exceptions import StateError
    from trw_mcp.formation import FormationError

    command = getattr(args, "formation_command", None)
    try:
        if command == "init":
            _run_init(args)
        elif command == "add-orchestrator":
            _run_add_orchestrator(args)
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
                "usage: trw-mcp formation {init|brief|status|add-orchestrator|add-slot|remove-slot|admit|set-status|pause|resume|watch|"
                "comms-upgrade|comms-rollback|merge}",
                file=sys.stderr,
            )
            sys.exit(2)
    except FormationError as exc:
        print(f"formation: {exc}", file=sys.stderr)
        sys.exit(1)
    except StateError as exc:  # INC-121 (a): no pinned run is a refusal with a remedy, not a traceback
        print(f"formation: {exc}; pin a run (trw_init / `trw-mcp run adopt`) or pass --run <run dir>", file=sys.stderr)
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
    """The run this session is pinned to; see :func:`_orchestrator_session`."""
    return _orchestrator_session(args)[0]


def _orchestrator_session(args: argparse.Namespace) -> tuple[Path, str]:
    """The pinned run and the pin key that FOUND it -- one derivation, never re-read from the env.

    The run a slot, outcome, or pause change is made AS: this session's pinned run (T29).

    Authority is never a caller-supplied path: any shell can name the
    orchestrator's run directory. The pin (keyed by TRW_SESSION_ID) is what this
    session's own trw_init recorded, so a member session cannot act as the
    orchestrator by passing its path. ``--run`` stays a mis-invocation guard: it
    must name the pinned run. No recency fallback either -- an unpinned session
    has no authority to borrow.
    """
    from trw_mcp.formation import FormationError
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths_pin_mgmt import get_pinned_run, run_path_for_pin

    context = build_call_context(None)
    if named := str(getattr(args, "session_id", None) or "").strip():
        # An explicit session is still authority only through the PIN STORE: it must own a run (feedback #144).
        pinned = run_path_for_pin(named)
        session_id = named
    else:
        pinned = get_pinned_run(context=context)
        session_id = context.session_id
    if pinned is None:
        raise FormationError("no_pinned_run: run trw_init (or trw_session_start) in the orchestrator session first")
    if (explicit := getattr(args, "run_path", None)) and Path(explicit).resolve() != pinned.resolve():
        raise FormationError(f"run_not_pinned: --run {explicit} is not this session's pinned run {pinned}")
    return pinned.resolve(), session_id


def _run_init(args: argparse.Namespace) -> None:
    from trw_mcp.formation import FormationError
    from trw_mcp.tools._orchestration_formation import create_formation, resolve_orchestrator_member_id

    payload_path = Path(args.from_file)
    try:
        raw = yaml.safe_load(payload_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FormationError(f"formation payload {payload_path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise FormationError(f"formation payload {payload_path} must contain a mapping")
    flag = getattr(args, "orchestrator_member_id", None)
    raw.update({} if flag is None else {"orchestrator_member_id": flag})
    # An addressable orchestrator binds THIS session's pin key, so the run must be the run this
    # session is pinned to: the key and the run come from one pin record, never an env value paired
    # with a caller-named path. Opting out records no session, so it keeps the plain run resolution.
    raw["orchestrator_member_id"] = resolve_orchestrator_member_id(raw)  # applies the FR11/FR12 gate
    run, pin_key = _orchestrator_session(args) if raw["orchestrator_member_id"] else (_resolve_run(args), None)
    manifest = create_formation(run, raw, None, pin_key=pin_key)
    print(f"formation {manifest.formation_id} created at revision {manifest.revision}")
    print(str(Path(manifest.orchestrator_run_path) / "formation.yaml"))


def _run_add_orchestrator(args: argparse.Namespace) -> None:
    from trw_mcp.formation import FormationError, add_orchestrator, load

    run, pin_key = _orchestrator_session(args)
    context = load(run)
    if context is None:
        raise FormationError(f"run {run} owns no formation")
    manifest = add_orchestrator(context.manifest.formation_id, run, args.member_id, pin_key=pin_key)
    print(f"formation {manifest.formation_id} add-orchestrator applied at revision {manifest.revision}")


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
    from trw_mcp.tools._formation_status_cli import run_status

    run_status(args, _resolve_run(args))


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
