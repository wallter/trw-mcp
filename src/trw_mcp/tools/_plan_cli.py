"""``trw-mcp plan ...`` — peer plan proposal and review (PRD-CORE-275).

WHY A CLI AND NOT A TOOL. A tool DEFINITION is paid in the system prompt of
every session of every client that loads the surface, called or not. These four
verbs would be four permanent taxes on a surface most sessions never touch. The
CLI costs nothing until it is run and reaches every supported client profile,
including those that cannot call MCP tools at all. The registered MCP tool set
is unchanged by this PRD and a parity test asserts it.

This module is an ADAPTER: it parses arguments, calls the facade, and prints.
It opens no socket, spawns no process, and imports nothing from comms — the
agent sends the printed body itself with `trw_send`, through the MCP session it
already has.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

__all__ = ["add_plan_subcommands", "run_plan"]


def add_plan_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``plan precheck|propose|review|verify``."""
    parser = subparsers.add_parser("plan", help="Propose and review peer work plans (PRD-CORE-275)")
    verbs = parser.add_subparsers(dest="plan_command")

    pre = verbs.add_parser("precheck", help="Who declares these paths? Advisory only.")
    pre.add_argument("paths", nargs="+", help="Repo-relative paths you intend to change")
    pre.add_argument("--run", dest="run_path", default=None, help="A run inside the formation")

    prop = verbs.add_parser("propose", help="Emit a canonical, digest-bound plan proposal")
    prop.add_argument("--plan-id", required=True, help="32 lowercase hex characters")
    prop.add_argument("--revision", type=int, required=True, help="Positive integer; bump on every change")
    prop.add_argument("--path", dest="paths", action="append", default=[], help="Repeatable")
    prop.add_argument("--test-path", dest="test_paths", action="append", default=[], help="Repeatable")
    prop.add_argument("--summary", required=True, help="What you intend to do")
    prop.add_argument("--run", dest="run_path", default=None, help="A run inside the formation")

    rev = verbs.add_parser("review", help="Verify a received proposal and emit a review")
    rev.add_argument("--body", required=True, help="File holding the proposal body, or - for stdin")
    rev.add_argument("--finding", dest="findings", action="append", default=[], help="Repeatable")
    rev.add_argument("--run", dest="run_path", default=None, help="A run inside the formation")

    ver = verbs.add_parser("verify", help="Is a received review about the plan you hold?")
    ver.add_argument("--review", required=True, help="File holding the review body, or - for stdin")
    ver.add_argument("--proposal", required=True, help="File holding YOUR current proposal, or -")


def run_plan(args: argparse.Namespace) -> None:
    """Dispatch one plan verb. Exits non-zero on every refusal."""
    from trw_mcp.formation import FormationError
    from trw_mcp.plan import PlanError

    command = getattr(args, "plan_command", None)
    handlers = {"precheck": _run_precheck, "propose": _run_propose, "review": _run_review, "verify": _run_verify}
    handler = handlers.get(str(command))
    if handler is None:
        print("usage: trw-mcp plan {precheck|propose|review|verify}", file=sys.stderr)
        sys.exit(2)
    try:
        handler(args)
    except PlanError as exc:
        # A closed reason code, never a traceback or an absolute path: this text
        # is read by another agent.
        print(f"plan: {exc}", file=sys.stderr)
        sys.exit(1)
    except FormationError:
        # The formation facade raises with the manifest's ABSOLUTE path in the
        # message. Re-raising it verbatim leaked the filesystem layout to a peer
        # and, uncaught, printed a traceback. The cause is reported as a closed
        # reason instead; the detail is deliberately dropped rather than trimmed,
        # because a trimmed path is still a path.
        print("plan: formation_unavailable: no readable formation for this run", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


#: Enough for a maximal body plus slack, small enough that a hostile or mistaken
#: producer cannot exhaust memory through an unbounded pipe.
MAX_INPUT_BYTES = 262_144


def _read_body(source: str) -> str:
    """Read one body, bounded, without naming the path back to the caller."""
    from trw_mcp.plan import PlanError, PlanRefusal

    if source == "-":
        raw = sys.stdin.read(MAX_INPUT_BYTES + 1)
    else:
        try:
            with Path(source).open("r", encoding="utf-8") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
        except OSError as exc:
            # The path is NOT echoed: an absolute path in a refusal is exactly
            # what NFR01 forbids, and the caller already knows what it passed.
            raise PlanError(PlanRefusal.NOT_JSON, "the body could not be read") from exc
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise PlanError(PlanRefusal.TOO_LARGE, f"input exceeds {MAX_INPUT_BYTES} bytes")
    return raw


def _manifest(args: argparse.Namespace) -> tuple[Any, Path]:
    """The formation manifest for this run, plus the project root."""
    from trw_mcp.formation import FormationError, load
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import resolve_project_root, resolve_run_path

    explicit = getattr(args, "run_path", None)
    run = Path(explicit).resolve() if explicit else resolve_run_path(None, context=build_call_context(None))
    context = load(run)
    if context is None:
        raise FormationError(f"run {run} belongs to no formation; plan review needs peers")
    return context.manifest, resolve_project_root()


def _emit_precheck(rows: list[Any], *, stream: Any = None) -> None:
    """Render the advisory rows.

    ``stream`` defaults to stderr. STDOUT CARRIES THE BODY AND NOTHING ELSE:
    an independent review found that `plan propose > proposal.json` wrote the
    precheck table above the JSON, so the file the next command read was not
    parseable. `plan precheck` is the one verb whose OUTPUT is the table, so it
    passes stdout explicitly.
    """
    from trw_mcp.plan import ADVISORY_NOTE

    target = stream if stream is not None else sys.stderr
    for row in rows:
        print(row.render(), file=target)
    print(ADVISORY_NOTE, file=sys.stderr)


def _run_precheck(args: argparse.Namespace) -> None:
    from trw_mcp.plan import precheck

    manifest, project_root = _manifest(args)
    # The table IS this verb's output, so it goes to stdout.
    _emit_precheck(precheck(manifest, list(args.paths), project_root), stream=sys.stdout)


def _check_local_bound(body: str) -> None:
    """Refuse a body over the LOCAL bound, before anything reaches stdout.

    The asymmetry is deliberate and is the honest shape. Over the local bound is
    a refusal: this process cannot responsibly emit it. UNDER the local bound is
    NOT a promise of delivery — admission policy is snapshotted per group when
    the group is created, so a group can retain a smaller bound than whatever
    this process reads today, and `trw_send` remains the only authority on
    whether a body is deliverable.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.plan import PlanError, PlanRefusal

    limit = int(get_config().comms_body_max_bytes)
    size = len(body.encode("utf-8"))
    if size > limit:
        raise PlanError(
            PlanRefusal.TOO_LARGE,
            f"body is {size} bytes, over this process's configured bound of {limit}",
        )


def _run_propose(args: argparse.Namespace) -> None:
    from trw_mcp.plan import build_proposal, encode, parse_proposal, precheck

    manifest, project_root = _manifest(args)
    rows = precheck(manifest, list(args.paths) + list(args.test_paths), project_root)
    body = encode(
        build_proposal(
            plan_id=args.plan_id,
            revision=int(args.revision),
            paths=list(args.paths),
            test_paths=list(args.test_paths),
            summary=args.summary,
        )
    )
    # Validate what we are about to emit through the same parser a peer will
    # use, so a malformed plan_id or an oversized summary fails HERE rather than
    # after it has been sent.
    parse_proposal(body)
    _check_local_bound(body)
    _emit_precheck(rows)
    print(body)


def _run_review(args: argparse.Namespace) -> None:
    from trw_mcp.plan import build_review, encode, parse_proposal, precheck

    proposal = parse_proposal(_read_body(args.body))
    manifest, project_root = _manifest(args)
    # The reviewer reruns the check ITSELF rather than trusting the sender's
    # reading; a review that echoed the sender's own precheck adds nothing.
    rows = precheck(manifest, list(proposal["paths"]) + list(proposal["test_paths"]), project_root)
    findings = list(args.findings) or [row.render() for row in rows]
    body = encode(
        build_review(
            plan_id=str(proposal["plan_id"]),
            revision=int(proposal["revision"]),
            digest=str(proposal["digest"]),
            findings=findings,
        )
    )
    _check_local_bound(body)
    print(body)


def _run_verify(args: argparse.Namespace) -> None:
    from trw_mcp.plan import Currency, classify, parse_proposal, parse_review, render

    review = parse_review(_read_body(args.review))
    proposal = parse_proposal(_read_body(args.proposal))
    verdict = classify(review, proposal)
    print(render(verdict, review))
    if verdict is not Currency.CURRENT:
        sys.exit(3)
