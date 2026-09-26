"""``trw-mcp profile explain`` (PRD-CORE-300 S11b).

Replaces the profile-explain MCP tool: the per-field layer attribution
of the resolved session profile, plus the tool surface the project config
resolves to. It calls the same service as ``trw_status(detail="surface")``
(``profile.explain_surface``), so the two report the same names. Read-only, so
it also runs under the reviewer role and inside a dispatched child.

Output is one JSON document with ``--json``, otherwise ``key: value`` lines.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

__all__ = ["add_profile_subcommands", "run_profile", "surface_detail"]

logger = structlog.get_logger(__name__)


def add_profile_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``profile explain``."""
    profile = subparsers.add_parser("profile", help="Explain the resolved session profile and tool surface")
    verbs = profile.add_subparsers(dest="profile_command")
    explain = verbs.add_parser(
        "explain",
        help="Show which config layer set each profile field, and the resolved tool surface",
    )
    explain.add_argument("--domain", default="", help='Override the inferred domain, e.g. "frontend"')
    explain.add_argument(
        "--task-type", dest="task_type", default="", help='Override the inferred task type, e.g. "bugfix"'
    )
    explain.add_argument("--prd-path", dest="prd_path", default="", help="Infers the domain when --domain is unset")
    explain.add_argument(
        "--task-name", dest="task_name", default="", help="Infers the task type when --task-type is unset"
    )
    explain.add_argument("--json", dest="as_json", action="store_true", help="Emit one JSON document")


def surface_detail(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
    overrides: dict[str, str | None] | None = None,
) -> dict[str, object]:
    """The resolved profile and tool surface for one session.

    ``trw_status(detail="surface")`` passes its call context; the CLI passes the
    ``TRW_SESSION_ID`` of the shell. Fail-open: a resolution error returns a
    structured ``error``, never raises, so status cannot crash on it.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.profile import explain_surface
        from trw_mcp.state._paths import find_active_run, resolve_trw_dir

        run_dir = find_active_run(context=context, session_id=session_id)
        return explain_surface(get_config(), run_dir=run_dir, trw_dir=resolve_trw_dir(), **(overrides or {}))
    except Exception as exc:  # justified: fail-open, status must never crash
        logger.warning("status_surface_detail_failed", error=str(exc))
        return {"error": str(exc), "fields": [], "layers_applied": []}


def _explain(args: argparse.Namespace) -> dict[str, object]:
    overrides = {
        "domain": args.domain or None,
        "task_type": args.task_type or None,
        "prd_path": args.prd_path or None,
        "task_name": args.task_name or None,
    }
    return surface_detail(session_id=os.environ.get("TRW_SESSION_ID") or None, overrides=overrides)


def run_profile(args: argparse.Namespace) -> None:
    """Dispatch ``profile explain``; refuse an unknown subcommand."""
    if str(getattr(args, "profile_command", None)) != "explain":
        print("usage: trw-mcp profile explain [--json]. Valid subcommands: explain.", file=sys.stderr)
        sys.exit(2)
    document = _explain(args)
    if args.as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            rendered = json.dumps(value, default=str) if isinstance(value, (dict, list)) else value
            print(f"{key}: {rendered}")
