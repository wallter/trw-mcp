"""``trw-mcp handoff`` handlers (PRD-CORE-347-FR05, PRD-CORE-348-FR01).

Belongs to the ``_subcommands.py`` facade (dispatched lazily via
``SUBCOMMAND_HANDLERS["handoff"]``). A thin shell over :mod:`trw_mcp.handoff`.
Exit codes follow the reference checker: 0 conforms, 1 findings, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trw_memory.safe_fs import UnsafeWriteError

from trw_mcp.handoff import (
    AhrInputError,
    AhrParseError,
    Finding,
    digest,
    load,
    seal,
    validate,
)

_INPUT_ERRORS = (
    AhrInputError,
    UnsafeWriteError,
    OSError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    ValueError,
    RecursionError,
)


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        print(json.dumps(finding.as_dict(), ensure_ascii=True, sort_keys=True))


def _load_handoff(handoff_path: str | None) -> dict[str, Any] | None:
    if not handoff_path:
        return None
    try:
        return load(handoff_path)
    except AhrParseError as exc:
        raise AhrInputError(f"--handoff unreadable: {exc}") from exc


def _check(path: str, handoff_path: str | None) -> list[Finding]:
    try:
        doc = load(path)
    except AhrParseError as exc:
        return [exc.finding]
    return validate(doc, _load_handoff(handoff_path))


def _write_atomic(path: Path, text: str) -> None:
    """Replace *path* (through a symlink, never replacing the link) via the descriptor-anchored safe writer.

    The record keeps the mode of the file it replaces. A refused unsafe component raises ``UnsafeWriteError``
    and lands in ``run_handoff``'s input-error branch (exit 2, nothing written).
    """
    from trw_mcp._checkout_write import write_checkout_file

    path = path.resolve()
    write_checkout_file(path.parent, path, text)


def _write_new(path: Path, text: str) -> None:
    """Create *path* through the safe writer, anchored at its deepest existing ancestor; never overwrite."""
    if path.exists() or path.is_symlink():
        raise AhrInputError(f"{path} exists; a handoff record is never overwritten (R-DOC-2)")
    from trw_mcp._checkout_write import write_checkout_file

    root = path.parent
    while not root.is_dir():
        root = root.parent
    write_checkout_file(root, path, text)


def _default_handoff_dir() -> Path:
    """``<active run>/handoffs`` when this session has a pinned run, else ``<project>/.trw/handoffs``."""
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import find_active_run, resolve_trw_dir

    # A CLI process has no MCP ctx: the pin key comes from TRW_SESSION_ID / the client session id.
    run = find_active_run(context=build_call_context(None))
    return run / "handoffs" if run is not None else resolve_trw_dir() / "handoffs"


def _new_out(arg: str | None, default: Path) -> Path:
    """The draft path, refusing a symlink at the named path itself (checked before ``resolve`` follows it)."""
    raw = Path(arg).expanduser() if arg else default
    if raw.is_symlink():
        raise AhrInputError(f"{raw} is a symlink; a draft is written to a plain path")
    return raw.resolve()


def _run_new(args: argparse.Namespace) -> int:
    from trw_mcp.handoff._scaffold import build_draft, new_handoff_id
    from trw_mcp.server._handoff_git import git_state, repo_root

    now = datetime.now(UTC)
    handoff_id = new_handoff_id(now)
    out = _new_out(args.out, _default_handoff_dir() / f"{handoff_id}.json")
    root = repo_root(Path.cwd())
    draft = build_draft(
        handoff_id=handoff_id,
        out_path=out,
        tier=args.tier,
        subject=args.subject,
        next_read=list(args.next_read),
        to_scope=args.to_scope,
        to_id=args.to_id,
        root=root,
        git=git_state(root, exclude_dir=out.parent, handoff_id=handoff_id),
        now=now,
    )
    if out.exists():
        raise AhrInputError(f"{out} exists; a handoff record is never overwritten (R-DOC-2)")
    if draft.sidecar is not None:
        _write_new(*draft.sidecar)
    _write_new(out, json.dumps(draft.doc, indent=2, ensure_ascii=False) + "\n")
    if root is None:
        print("note: not a git work tree; as_of.base_ref.tree_state is 'unknown'", file=sys.stderr)
    print(out)
    return 0


def _check_report(record: Path, expected: str | None) -> dict[str, Any]:
    from trw_mcp.handoff._check import check_record
    from trw_mcp.server._handoff_git import commits_since, git_state, repo_root

    record = record.resolve()
    root = repo_root(record.parent) if record.parent.is_dir() else None
    handoff_id = load(record).get("handoff_id")
    git = git_state(root, exclude_dir=record.parent, handoff_id=handoff_id if isinstance(handoff_id, str) else None)
    return check_record(
        record,
        expected_digest=expected,
        now=datetime.now(UTC),
        root=root,
        git=git,
        commits_since=(lambda commit: commits_since(root, commit)) if root is not None else lambda _c: None,
    )


def _run_check(args: argparse.Namespace) -> int:
    from trw_mcp.handoff._check import is_clean, summary

    if args.digest is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.digest):
        raise AhrInputError("--digest must be sha256:<64 lowercase hex>")
    report = _check_report(Path(args.file), args.digest)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    for line in summary(report):
        print(line, file=sys.stderr)
    return 0 if is_clean(report) else 1


def _run_readback_new(args: argparse.Namespace) -> int:
    from trw_mcp.handoff._check import summary
    from trw_mcp.handoff._readback_scaffold import build_readback_draft
    from trw_mcp.handoff._scaffold import harness, sender_id

    record = Path(args.file).resolve()
    handoff = load(record)
    if handoff.get("type") != "handoff" or validate(handoff):
        raise AhrInputError("readback-new takes a valid handoff record (run `trw-mcp handoff check` first)")
    report = _check_report(record, None)
    draft = build_readback_draft(
        handoff,
        report,
        sender=sender_id(),
        harness_name=harness(),
        now=datetime.now(UTC),
        as_addressee=args.as_addressee,
    )
    default = record.with_name(f"{handoff['handoff_id']}.readback.{draft['readback_id']}.json")
    out = _new_out(args.out, default)
    _write_new(out, json.dumps(draft, indent=2, ensure_ascii=False) + "\n")
    for line in summary(report):
        print(line, file=sys.stderr)
    print(out)
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    findings = _check(args.file, args.handoff)
    _print_findings(findings)
    return 1 if findings else 0


def _run_digest(args: argparse.Namespace) -> int:
    print(digest(load(args.file)))
    return 0


def _run_seal(args: argparse.Namespace) -> int:
    path = Path(args.file)
    sealed = seal(load(path))
    findings = validate(sealed, _load_handoff(args.handoff))
    if findings:
        _print_findings(findings)
        print(f"refused: {path} fails validation; nothing written", file=sys.stderr)
        return 1
    _write_atomic(path, json.dumps(sealed, indent=2, ensure_ascii=False) + "\n")
    print(sealed["integrity"]["digest"])
    return 0


def _run_render(args: argparse.Namespace) -> int:
    from trw_mcp.handoff._render import render_markdown

    doc = load(args.file)
    if doc.get("type") != "handoff":
        raise AhrInputError("render takes a handoff record")
    findings = validate(doc)
    if findings:
        _print_findings(findings)
        print(f"refused: {args.file} fails validation; not rendered", file=sys.stderr)
        return 1
    sys.stdout.write(render_markdown(doc))
    return 0


_HANDLERS = {
    "new": _run_new,
    "readback-new": _run_readback_new,
    "check": _run_check,
    "validate": _run_validate,
    "digest": _run_digest,
    "seal": _run_seal,
    "render": _run_render,
}


def run_handoff(args: argparse.Namespace) -> None:
    """Dispatch ``trw-mcp handoff <verb>``; exits 0, 1 (findings) or 2 (input error)."""
    handler = _HANDLERS.get(getattr(args, "handoff_command", None) or "")
    if handler is None:
        print("usage: trw-mcp handoff {new,readback-new,validate,digest,seal,render,check} ...", file=sys.stderr)
        sys.exit(2)
    try:
        code = handler(args)
    except AhrParseError as exc:
        _print_findings([exc.finding])
        code = 1
    except _INPUT_ERRORS as exc:
        print(f"input error: {exc}", file=sys.stderr)
        code = 2
    sys.exit(code)
