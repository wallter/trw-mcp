"""``trw-mcp instructions sync`` (PRD-CORE-300, slice S6b).

The former standalone instructions-sync tool was an MCP tool. Syncing the TRW protocol block into
a client's instruction file is a rare operator/onboarding action (new project,
protocol-template change, IDE switch), so it is a CLI verb over the same
``execute_claude_md_sync`` implementation ``trw_deliver`` already calls
directly for its own instruction-sync step (that call site is untouched by
this cut).

Output is one JSON document with ``--json``, otherwise ``key: value`` lines.
The exit status is 1 when ``status`` is ``"refused"`` and 0 otherwise; a
``"dry_run"`` or ``"unchanged"`` result is a result, not a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = ["add_instructions_subcommands", "run_instructions"]


def add_instructions_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``instructions sync``."""
    instructions = subparsers.add_parser("instructions", help="Instruction-file maintenance verbs")
    verbs = instructions.add_subparsers(dest="instructions_command")
    sync = verbs.add_parser(
        "sync", help="Sync TRW protocol + ceremony guidance into the client's instruction file; state-changing"
    )
    sync.add_argument("--scope", default="root", help='"root" for the project instruction file, "sub" for module-level')
    sync.add_argument("--target-dir", default=None, help="Where to write the sub-scope file")
    sync.add_argument(
        "--client",
        default="auto",
        help='"auto" detects from IDE config dirs, a specific client name, or "all"',
    )
    sync.add_argument("--dry-run", action="store_true", help="Report a unified diff per target; write nothing")
    sync.add_argument("--force", action="store_true", help="Write even if the guard detects content loss")
    sync.add_argument("--json", dest="as_json", action="store_true")


def _sync(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync, instruction_write_trigger
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._learning_module_helpers import _create_llm_client

    config = get_config()
    reader = FileStateReader()
    llm = _create_llm_client()
    with instruction_write_trigger("tool_call", "instructions sync"):
        result: dict[str, Any] = dict(
            execute_claude_md_sync(
                args.scope,
                args.target_dir,
                config,
                reader,
                llm,
                args.client,
                dry_run=bool(args.dry_run),
                force=bool(args.force),
            )
        )
    return result, result.get("status") == "refused"


def run_instructions(args: argparse.Namespace) -> None:
    """Dispatch ``instructions sync``."""
    handler = {"sync": _sync}.get(str(args.instructions_command))
    if handler is None:
        print("usage: trw-mcp instructions {sync}", file=sys.stderr)
        sys.exit(2)
    document, failed = handler(args)
    if args.as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(1 if failed else 0)
