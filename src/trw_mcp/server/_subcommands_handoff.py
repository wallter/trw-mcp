"""``trw-mcp handoff`` handlers (PRD-CORE-347-FR05, PRD-CORE-348-FR01).

Belongs to the ``_subcommands.py`` facade (dispatched lazily via
``SUBCOMMAND_HANDLERS["handoff"]``). A thin shell over :mod:`trw_mcp.handoff`.
Exit codes follow the reference checker: 0 conforms, 1 findings, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
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
    "validate": _run_validate,
    "digest": _run_digest,
    "seal": _run_seal,
    "render": _run_render,
}


def run_handoff(args: argparse.Namespace) -> None:
    """Dispatch ``trw-mcp handoff <verb>``; exits 0, 1 (findings) or 2 (input error)."""
    handler = _HANDLERS.get(getattr(args, "handoff_command", None) or "")
    if handler is None:
        print("usage: trw-mcp handoff {validate,digest,seal,render} FILE", file=sys.stderr)
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
