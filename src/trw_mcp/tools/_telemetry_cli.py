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
over the four compounding-pipeline signals. It exits 1 only when the probe
itself could not run (``measured: False`` at the top level); a degraded signal
is a result, reported in the document. The fail-closed gate on degradation is
``make check`` (``pipeline-health`` target), a deliberately stricter contract.

Output is one JSON document with ``--json``, otherwise ``key: value`` lines.
None of the S3a five ever exits non-zero: each is a pure read of already-persisted
state, and a "not found" / "no activity" / "could not resolve" outcome is a
reported result, not an execution failure — matching what each did as an MCP
tool, none of which ever raised.
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
    surface_diff.add_argument("--snapshot-id-a", required=True)
    surface_diff.add_argument("--snapshot-id-b", required=True)

    security = verbs.add_parser("security", help="MCP security/trust-boundary status (read-only)")

    channel_stats = verbs.add_parser("channel-stats", help="Per-channel push->outcome correlation (read-only)")
    channel_stats.add_argument("--window-hours", type=int, default=1)
    channel_stats.add_argument("--repo-root", default=None)

    health = verbs.add_parser("pipeline-health", help="Report the four compounding-pipeline health signals")

    for parser in (events, classify, surface_diff, security, channel_stats, health):
        parser.add_argument("--json", dest="as_json", action="store_true")


def _emit(document: dict[str, Any], *, as_json: bool, failed: bool) -> None:
    if as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(1 if failed else 0)


def _events(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.tools.query_tools import query_events

    filters: dict[str, Any] = {}
    if args.run_id is not None:
        filters["run_id"] = args.run_id
    if args.event_type is not None:
        filters["event_type"] = args.event_type
    if args.emitter is not None:
        filters["emitter"] = args.emitter
    return query_events(session_id=args.session_id, filters=filters or None), False


def _classify(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.meta_tune.surface_registry import classify_path

    classification = classify_path(Path(args.path))
    return {
        "path": args.path,
        "classification": "control" if classification.is_control else "advisory",
        "surfaces": [surface.value for surface in classification.surfaces],
        "rationale": classification.rationale,
    }, False


def _surface_diff(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.tools.query_tools import surface_diff

    # "snapshot_not_found" is a legitimate result (like a rejected promotion),
    # not an execution failure — this never exits non-zero, matching the
    # original tool, which never raised either.
    result = surface_diff(snapshot_id_a=args.snapshot_id_a, snapshot_id_b=args.snapshot_id_b)
    return result, False


def _security(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    del args
    return security_status_document(), False


def security_status_document() -> dict[str, Any]:
    """The ``telemetry security`` payload — also read by the ``doctor`` row.

    Always the events-directory fallback (see module docstring): a CLI
    invocation never has a live in-process ``MCPSecurityMiddleware`` to snapshot.
    """
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools.mcp_security_status import compute_security_status

    trw_dir = resolve_trw_dir()
    return compute_security_status(events_dir=trw_dir / "context").model_dump()


def _channel_stats(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.tools.channel_stats import compute_channel_stats_result

    # Never raises and never exits non-zero, matching the original tool: an
    # unresolvable repo root or an empty/missing log is a reported status, not
    # a CLI execution failure.
    result = compute_channel_stats_result(window_hours=args.window_hours, repo_root=args.repo_root)
    return result, False


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

        return step_pipeline_health(resolve_trw_dir())
    except Exception as exc:  # justified: fail-open, this must never crash a caller
        logger.warning("pipeline_health_cli_failed", error=str(exc))
        return {
            "degraded": False,
            "measured": False,
            "advisory": "health_probe_failed",
            "error": str(exc),
            "sync_push": dict(_UNMEASURED_SIGNAL),
            "graph_edges": dict(_UNMEASURED_SIGNAL),
            "embedding_coverage": dict(_UNMEASURED_SIGNAL),
            "recall_feedback": dict(_UNMEASURED_SIGNAL),
        }


def _pipeline_health(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    del args
    document = safe_pipeline_health()
    return document, document.get("measured") is False


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
    document, failed = handler(args)
    _emit(document, as_json=bool(getattr(args, "as_json", False)), failed=failed)


__all__ = ["add_telemetry_subcommands", "run_telemetry", "safe_pipeline_health", "security_status_document"]
