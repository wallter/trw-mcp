"""``trw-mcp code index`` / ``trw-mcp code risk`` handlers (PRD-CORE-300-FR06 slice S4).

Replaces two former MCP tools under the FR02 CLI-replacement contract: the
code-index build tool (state-changing: the sole writer of the code-index
store) and the codebase-risk-report tool (read-only). Both call the SAME
pure callables the removed tools wrapped — :func:`build_code_index` and
:func:`compute_codebase_risk_report` — so behavior is unchanged; only the
transport is.
"""

from __future__ import annotations

import argparse
import json
import sys


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _run_code_index(args: argparse.Namespace) -> None:
    from trw_mcp.tools.code_index import build_code_index

    result = build_code_index(
        repo_root=args.repo_root,
        force=bool(args.force),
        paths=args.paths,
    )
    if args.as_json:
        _print_json(result)
    elif result.get("status") == "ok":
        stats = result.get("stats", {})
        print(f"Status: ok\nManifest: {result.get('manifest_path')}\nStats: {stats}")
    else:
        print(f"Status: failed\nError: {result.get('error')}", file=sys.stderr)

    if result.get("status") != "ok":
        sys.exit(1)


def _run_code_risk(args: argparse.Namespace) -> None:
    from trw_mcp.tools.codebase_risk_report import compute_codebase_risk_report

    result = compute_codebase_risk_report(
        repo_root=args.repo_root,
        cache_dir=args.cache_dir,
        top_n=args.top_n,
    )
    payload = result.model_dump()
    if args.as_json:
        _print_json(payload)
    else:
        print(
            f"Status: {result.distill_status}\nEntries: {result.n_scores}\nAction: {result.distill_action or '(none)'}"
        )


_CODE_HANDLERS: dict[str, object] = {
    "index": _run_code_index,
    "risk": _run_code_risk,
}


def run_code(args: argparse.Namespace) -> None:
    """Dispatch ``trw-mcp code <index|risk>``; refuse an unknown subcommand."""
    command = getattr(args, "code_command", None)
    handler = _CODE_HANDLERS.get(str(command))
    if handler is None:
        valid = ", ".join(sorted(_CODE_HANDLERS))
        print(
            f"Unknown 'code' subcommand: {command!r}. Valid subcommands: {valid}.",
            file=sys.stderr,
        )
        sys.exit(1)
    handler(args)  # type: ignore[operator]


__all__ = ["run_code"]
