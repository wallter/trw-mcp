"""``trw-mcp telemetry ...`` — PRD-CORE-300-FR04 slice S3a, FR05 slice S3b.

Six MCP tools moved here as read-only CLI verbs: ``events``, ``classify``,
``surface-diff``, ``security`` (also surfaced as a ``trw-mcp doctor`` row),
and ``channel-stats`` (S3a); ``pipeline-health`` (S3b, also a doctor row). All five are read-only queries over already-persisted
state (events files, surface snapshots, the SAFE-001 surface registry, or
channel telemetry) — none write, so none refuse under the reviewer role or a
dispatched child (FR02's state-changing guard never applies here). See
``server/_cli_replacements.py::CLI_REPLACEMENTS`` for the exact tool name each
verb replaced.

A CLI invocation is a fresh process, so ``telemetry security`` cannot read a
live server's in-memory ``MCPSecurityMiddleware`` snapshot the way the MCP tool
did when one was running in the SAME process. It always uses
:func:`trw_mcp.tools.mcp_security_status.compute_security_status`'s
events-directory fallback, which is what the tool itself fell back to whenever
no server middleware was resolvable (e.g. every offline/test invocation
already exercised this path).

``pipeline-health`` runs :func:`trw_mcp.tools._pipeline_health.step_pipeline_health`
over the four compounding-pipeline signals and exits 0 healthy, 1 degraded, 2 unknown (nothing measured, or the
probe crashed) -- E2E-INC-073; ``make check`` (``pipeline-health`` target) is the fail-closed gate.

Output is one JSON document with ``--json``, otherwise ``key: value`` lines.
Every other verb exits 0 for a result (including "no activity") and 2 for misuse (INC-074): a
``surface-diff`` snapshot id that does not exist, a ``--window-hours`` that is not a positive integer,
or a ``--repo-root`` that is not a directory. Misuse used to print a result and exit 0, so a script
could not tell a typo from an answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_UNMEASURED_SIGNAL: dict[str, Any] = {"degraded": False, "measured": False, "advisory": "probe_error"}


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive number of hours, got {value}")
    return value


def _existing_dir(text: str) -> str:
    if not Path(text).is_dir():
        raise argparse.ArgumentTypeError(f"{text!r} is not a directory")
    return text


def add_telemetry_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``telemetry events|classify|surface-diff|security|channel-stats|pipeline-health``."""
    telemetry = subparsers.add_parser("telemetry", help="Read-only telemetry, surface-security and pipeline-health")
    verbs = telemetry.add_subparsers(dest="telemetry_command")

    events = verbs.add_parser("events", help="Merged cross-emitter event view for a session (read-only)")
    events.add_argument("--session-id", default=None, help="Restrict to this session; omit for cross-session")
    events.add_argument("--run-id", default=None, help="Extra equality filter")
    events.add_argument("--event-type", default=None, help="Extra equality filter")
    events.add_argument("--emitter", default=None, help="Extra equality filter")

    classify = verbs.add_parser("classify", help="Classify a path as SAFE-001 control or advisory (read-only)")
    classify.add_argument("--path", required=True, help="Repository-relative or absolute path to classify")

    surface_diff = verbs.add_parser("surface-diff", help="Diff two recorded surface snapshots (read-only)")
    surface_diff.add_argument("--snapshot-id-a", required=True, help="The first surface snapshot to compare")
    surface_diff.add_argument("--snapshot-id-b", required=True, help="The second surface snapshot to compare")

    security = verbs.add_parser("security", help="MCP security/trust-boundary status (read-only)")

    channel_stats = verbs.add_parser("channel-stats", help="Per-channel push->outcome correlation (read-only)")
    channel_stats.add_argument(
        "--window-hours", type=_positive_int, default=1, help="How many recent hours to count (default 1)"
    )
    channel_stats.add_argument(
        "--repo-root", type=_existing_dir, default=None, help="Repository to read (default: this project)"
    )

    health = verbs.add_parser("pipeline-health", help="Report the four compounding-pipeline health signals")

    for parser in (events, classify, surface_diff, security, channel_stats, health):
        parser.add_argument("--json", dest="as_json", action="store_true", help="Print one JSON document")


def _emit(document: dict[str, Any], *, as_json: bool, exit_code: int) -> None:
    if as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(exit_code)


def _events(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from trw_mcp.tools.query_tools import query_events

    filters: dict[str, Any] = {}
    if args.run_id is not None:
        filters["run_id"] = args.run_id
    if args.event_type is not None:
        filters["event_type"] = args.event_type
    if args.emitter is not None:
        filters["emitter"] = args.emitter
    return query_events(session_id=args.session_id, filters=filters or None), 0


def _classify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from trw_mcp.meta_tune.surface_registry import classify_path

    classification = classify_path(Path(args.path))
    return {
        "path": args.path,
        "classification": "control" if classification.is_control else "advisory",
        "surfaces": [surface.value for surface in classification.surfaces],
        "rationale": classification.rationale,
    }, 0


def _surface_diff(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from trw_mcp.tools.query_tools import surface_diff

    # INC-074: a snapshot id that does not exist is misuse (a typo or a stale id), so exit 2 -- the document
    # still says which side was missing.
    result = surface_diff(snapshot_id_a=args.snapshot_id_a, snapshot_id_b=args.snapshot_id_b)
    return result, 2 if result.get("error") == "snapshot_not_found" else 0


def _security(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    del args
    return security_status_document(), 0


def security_status_document() -> dict[str, Any]:
    """The ``telemetry security`` payload — also read by the ``doctor`` row.

    Always the events-directory fallback (see module docstring): a CLI
    invocation never has a live in-process ``MCPSecurityMiddleware`` to snapshot.
    """
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools.mcp_security_status import compute_security_status

    trw_dir = resolve_trw_dir()
    return compute_security_status(events_dir=trw_dir / "context").model_dump()


def _channel_stats(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from trw_mcp.tools.channel_stats import compute_channel_stats_result

    # An empty/missing log is a reported status (exit 0); a bad --window-hours / --repo-root is refused by
    # argparse (exit 2) before this runs.
    result = compute_channel_stats_result(window_hours=args.window_hours, repo_root=args.repo_root)
    return result, 0


def safe_pipeline_health() -> dict[str, Any]:
    """The live project's pipeline-health verdict; never raises.

    Shared by the CLI and the ``trw-mcp doctor`` row so both report the same
    fail-open shape on a crash (PRD-CORE-263 DEF-06): every entry carries
    ``measured: False``, top level and per signal, rather than rendering like a
    healthy aggregate to a caller that reads ``degraded`` alone.
    """
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.tools._pipeline_health import step_pipeline_health

        return step_pipeline_health(resolve_trw_dir(), self_hint=False)
    except Exception as exc:  # justified: fail-open, this must never crash a caller
        logger.warning("pipeline_health_cli_failed", error=str(exc))
        return {
            "degraded": False,
            "status": "unknown",
            "measured": False,
            "advisory": "health_probe_failed",
            "error": str(exc),
            "sync_push": dict(_UNMEASURED_SIGNAL),
            "graph_edges": dict(_UNMEASURED_SIGNAL),
            "embedding_coverage": dict(_UNMEASURED_SIGNAL),
            "recall_feedback": dict(_UNMEASURED_SIGNAL),
        }


#: ``pipeline-health`` exit codes (lead ruling, E2E-INC-073): a script must never read "unmeasured" as healthy.
PIPELINE_HEALTH_EXIT: dict[str, int] = {"healthy": 0, "degraded": 1, "unknown": 2}


def _pipeline_health(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """0 healthy, 1 degraded, 2 unknown (no probe measured, or the probe run crashed) -- same with ``--json``."""
    del args
    document = safe_pipeline_health()
    return document, PIPELINE_HEALTH_EXIT.get(str(document.get("status")), 2)


_HANDLERS: dict[str, Any] = {
    "events": _events,
    "classify": _classify,
    "surface-diff": _surface_diff,
    "security": _security,
    "channel-stats": _channel_stats,
    "pipeline-health": _pipeline_health,
}


def run_telemetry(args: argparse.Namespace) -> None:
    """Dispatch ``telemetry events|classify|surface-diff|security|channel-stats|pipeline-health``."""
    command = str(getattr(args, "telemetry_command", None) or "")
    handler = _HANDLERS.get(command)
    if handler is None:
        print(f"usage: trw-mcp telemetry {{{'|'.join(_HANDLERS)}}}", file=sys.stderr)
        sys.exit(2)
    document, exit_code = handler(args)
    _emit(document, as_json=bool(getattr(args, "as_json", False)), exit_code=exit_code)


__all__ = ["add_telemetry_subcommands", "run_telemetry", "safe_pipeline_health", "security_status_document"]
