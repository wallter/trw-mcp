"""``trw-mcp handoff`` subparsers (PRD-CORE-347-FR05, PRD-CORE-348-FR01).

Belongs to the ``_cli_argparse.py`` facade. Registers the ``handoff`` verb group
(subparser dest ``handoff_command``) for Agent Handoff Records: ``validate``,
``digest``, ``render`` and ``check`` are read-only; ``seal`` writes only the named file;
``new`` and ``readback-new`` write only a fresh draft (``new`` also its changed-paths sidecar),
never over an existing file.
"""

from __future__ import annotations

import argparse

__all__ = ["add_handoff_subcommands"]


def add_handoff_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``handoff new|readback-new|validate|digest|seal|render|check``."""
    parser = subparsers.add_parser(
        "handoff",
        help="Draft, validate, digest, seal, render or check an Agent Handoff Record (AHR 1.0-rc.2)",
    )
    sub = parser.add_subparsers(dest="handoff_command")

    new = sub.add_parser(
        "new",
        help="Write a DRAFT handoff: ids, times, git state and pointer digests filled in, "
        "judgement fields as TODO(handoff): sentinels",
    )
    new.add_argument("--tier", choices=("minimal", "standard", "critical"), default="minimal")
    new.add_argument("--subject", required=True, help="Stable slug for the work item, reused by superseding records")
    new.add_argument(
        "--next-read",
        action="append",
        default=[],
        metavar="PATH",
        help="A file inside the repository the receiver reads (repeatable, in reading order; a file: URI is "
        "repo-relative); digested over its raw bytes. https: and trw: URIs are carried undigested",
    )
    new.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="GLOB",
        help="A repository-relative path or glob (*, **, ?) the receiver may change (repeatable): objective.paths",
    )
    new.add_argument(
        "--constraint",
        action="append",
        default=[],
        metavar="TEXT",
        help="A constraint's operative wording, carried verbatim into constraints[] (repeatable); "
        "paste the operator's words, never a summary",
    )
    new.add_argument(
        "--constraint-from",
        action="append",
        default=[],
        metavar="PATH#L<first>[-L<last>]",
        help="Copy a constraint from those lines of a repository file, bound to the file's digest (repeatable); "
        "`handoff check` later verifies the text is still a verbatim part of that source",
    )
    new.add_argument("--to-scope", default="next-session", help="Scope of the unaddressed receiver")
    new.add_argument(
        "--to-id",
        default=None,
        metavar="ID",
        help="Address the record to this agent (e.g. codex:next or a peer member id); "
        "critical defaults to <harness>:next, with the operator as verifier",
    )
    new.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Draft path (default: <active run>/handoffs/<id>.json, else .trw/handoffs/<id>.json)",
    )

    rbnew = sub.add_parser(
        "readback-new",
        help="Write a DRAFT read-back for a handoff: id, author, time, handoff digest and pointer checks "
        "filled in, restatements and re-verification results as TODO(handoff): sentinels",
    )
    rbnew.add_argument("file", help="The AHR handoff JSON file being received")
    rbnew.add_argument(
        "--out", default=None, metavar="PATH", help="Draft path (default: <id>.readback.<readback_id>.json beside it)"
    )
    rbnew.add_argument(
        "--as-addressee",
        action="store_true",
        help="The user confirmed this session is the principal the record is addressed to",
    )

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

    brief = sub.add_parser(
        "brief",
        help="Project a record (draft or sealed) into one read-only sweep brief per item: commit, objective.paths "
        "and constraints carried verbatim, a fixed item ledger and the return schema; never truncates",
    )
    brief.add_argument("file", help="AHR handoff JSON file (a draft is enough: only scope and constraints are read)")
    brief.add_argument(
        "--items", required=True, metavar="FILE", help='JSON array of {"id","question","hint"?,"must_contain"?}'
    )
    brief.add_argument("--max-reads", type=int, default=6, help="Read budget stated to each helper (default 6)")
    brief.add_argument("--out-dir", default=None, metavar="DIR", help="Write <id>.brief.md files here")

    brief_check = sub.add_parser(
        "brief-check",
        help="Check helper results against the same items: one row per id, allowed status and label, every "
        "cited line present in scope exactly as quoted; anything else is inconclusive. Exit 1 unless every "
        "item is citation_valid and no row names an unknown item (citation_valid is not a judgement that the "
        "answer is right)",
    )
    brief_check.add_argument("file", help="The AHR handoff JSON file the briefs were rendered from")
    brief_check.add_argument("--items", required=True, metavar="FILE", help="The items file given to `brief`")
    brief_check.add_argument(
        "--results", required=True, metavar="FILE", help="Helper rows: a JSON array, or one JSON object per line"
    )

    check = sub.add_parser(
        "check",
        help="Receiver pre-flight: digest, validity, expiry, supersession, pointer digests and git state; "
        "JSON on stdout, summary on stderr, exit 1 on any finding",
    )
    check.add_argument("file", help="AHR handoff JSON file")
    check.add_argument("--digest", default=None, metavar="sha256:HEX", help="The digest the sender gave you")
    check.add_argument(
        "--strict",
        action="store_true",
        help="Accept only positive evidence: the given digest matches, every pointer and sourced constraint is "
        "match, and the checkout is where the sender left it; unknowns (not_accessed, no_digest) are findings. "
        "Use before acting on a record without a person reading the report",
    )
