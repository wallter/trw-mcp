"""``trw-mcp probe run|budget`` and ``trw-mcp meta-tune propose|rollback`` (PRD-CORE-300-FR04).

These were four MCP tools. A tool definition is paid in every session's prompt
whether or not it is called, and these are rare operator actions, so they are
CLI verbs that run the same implementations with the same arguments.

A probe run's budget and result cache used to live in the server process for
the life of a session. Each CLI invocation is its own process, so the run's
state is kept in ``.trw/runtime/probe/<run id>.json`` instead, and the budget
and cache still hold across the probes of one run (PRD-CORE-144 FR-07, FR-08).
``probe budget`` reads that file and never creates it (FR-10).

Output is one JSON document with ``--json``, otherwise ``key: value`` lines.
The exit status is 1 when the document reports an error and 0 otherwise; a
rejected promotion or a disabled subsystem is a result, not an error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["add_experiment_subcommands", "run_meta_tune", "run_probe"]

_DEFAULT_MODE = "TRIANGULATED_WITH_PROBE"
_UNSAFE_RUN_ID = re.compile(r"[^A-Za-z0-9_.-]")


def add_experiment_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``probe run|budget`` and ``meta-tune propose|rollback``."""
    probe = subparsers.add_parser("probe", help="Sandboxed experiments that settle a plan assumption (PRD-CORE-144)")
    probe_verbs = probe.add_subparsers(dest="probe_command")
    run = probe_verbs.add_parser("run", help="Run a sandboxed probe; gated by TRW_PROBE_ENABLED")
    run.add_argument("--hypothesis", required=True, help="The claim the probe checks")
    run.add_argument("--command", dest="probe_argv", required=True, help="The command to run, as one shell string")
    run.add_argument("--timeout-s", type=int, default=30)
    run.add_argument("--memory-mb", type=int, default=256)
    run.add_argument("--allow-network", action="store_true")
    run.add_argument("--hypothesis-id", default=None)
    budget = probe_verbs.add_parser("budget", help="Report a run's probe budget usage; read-only")
    for parser in (run, budget):
        parser.add_argument("--run-id", default="unknown")
        parser.add_argument("--planning-mode", default=_DEFAULT_MODE)
        parser.add_argument("--json", dest="as_json", action="store_true")

    meta = subparsers.add_parser("meta-tune", help="SAFE-001 promotion and rollback; Linux only")
    meta_verbs = meta.add_subparsers(dest="meta_tune_command")
    propose = meta_verbs.add_parser("propose", help="Promote a candidate through sandbox and review")
    propose.add_argument("--target-path", required=True)
    content = propose.add_mutually_exclusive_group(required=True)
    content.add_argument("--candidate-content")
    content.add_argument("--candidate-file", help="Read the candidate content from this file")
    propose.add_argument("--proposer-id", required=True)
    propose.add_argument("--sandbox-command", required=True, help="The replay command, as one shell string")
    propose.add_argument("--reviewer-id", default=None)
    propose.add_argument("--approval-ts", default=None, help="ISO 8601 timestamp")
    propose.add_argument("--declared-metric-delta", type=float, default=None)
    propose.add_argument("--promotion-session-id", default=None)
    rollback = meta_verbs.add_parser("rollback", help="Restore a promoted proposal's prior content")
    rollback.add_argument("--proposal-id", required=True)
    rollback.add_argument("--audit-log-path", default=None)
    for parser in (propose, rollback):
        parser.add_argument("--state-dir", default=None)
        parser.add_argument("--json", dest="as_json", action="store_true")


def _emit(document: dict[str, Any], *, as_json: bool, failed: bool) -> None:
    if as_json:
        print(json.dumps(document, default=str))
    else:
        for key, value in document.items():
            print(f"{key}: {value}")
    sys.exit(1 if failed else 0)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _state_path(run_id: str) -> Path:
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir() / "runtime" / "probe" / f"{_UNSAFE_RUN_ID.sub('_', run_id)}.json"


def _load_state(run_id: str, planning_mode: str) -> tuple[Any, Any]:
    """The run's budget and cache; a run's first probe pins its planning mode."""
    from trw_mcp.models.probe import ProbeResult
    from trw_mcp.probe.budget import ProbeBudget
    from trw_mcp.probe.cache import ProbeCache

    path = _state_path(run_id)
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    budget = ProbeBudget(saved.get("planning_mode", planning_mode))
    budget.used = int(saved.get("used", 0))
    budget.by_hypothesis_id = dict(saved.get("by_hypothesis_id", {}))
    cache = ProbeCache()
    for key, result in saved.get("results", {}).items():
        cache.put(key, ProbeResult.model_validate(result))
    return budget, cache


def _save_state(run_id: str, budget: Any, cache: Any) -> None:
    from trw_mcp.state.persistence import FileStateWriter

    document = {
        "planning_mode": budget.planning_mode,
        "used": budget.used,
        "by_hypothesis_id": budget.by_hypothesis_id,
        "results": {key: result.model_dump(mode="json") for key, result in cache.items()},
    }
    path = _state_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    FileStateWriter().write_text(path, json.dumps(document))


def _publish_probe_event(event: Any) -> None:
    """Fire-and-forget: a probe never fails because telemetry is unavailable (FR-09)."""
    import structlog

    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        TelemetryPipeline.get_instance().enqueue(event.model_dump(mode="json"))
    except Exception:  # justified: fail-open, telemetry must not block the probe
        structlog.get_logger(__name__).debug("probe_event_publish_failed", exc_info=True)


def _probe_run(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.models.probe import ResourceBudget
    from trw_mcp.probe.budget import ProbeBudgetExhausted
    from trw_mcp.probe.cache import probe_cache_key
    from trw_mcp.probe.harness import ProbeValidationError, run_probe
    from trw_mcp.probe.telemetry import build_probe_event

    if not _flag("TRW_PROBE_ENABLED"):
        return {
            "error": "probe_disabled",
            "reason": "probes are gated OFF (CORE-144 §9 Phase 1)",
            "remediation": "set TRW_PROBE_ENABLED=1 to enable empirical probes",
        }, True
    budget, cache = _load_state(args.run_id, args.planning_mode)
    key = probe_cache_key(command=args.probe_argv, hypothesis=args.hypothesis, hypothesis_id=args.hypothesis_id)
    cached = cache.get(key)
    if cached is not None:
        return cached.model_dump(mode="json"), False
    try:
        # The slot is reserved and saved BEFORE spawn (FR-07 A1), so a crash cannot un-spend it.
        used_override = budget.consume(hypothesis_id=args.hypothesis_id, override=_flag("TRW_PROBE_BUDGET_OVERRIDE"))
    except ProbeBudgetExhausted as exc:
        return {
            "error": "probe_budget_exhausted",
            "planning_mode": exc.planning_mode,
            "total": exc.total,
            "remaining": exc.remaining,
            "override_hint": exc.override_hint,
        }, True
    _save_state(args.run_id, budget, cache)
    try:
        result = run_probe(
            hypothesis=args.hypothesis,
            command=args.probe_argv,
            run_id=args.run_id,
            timeout_s=args.timeout_s,
            resource_budget=ResourceBudget(memory_mb=args.memory_mb),
            allow_network=args.allow_network,
            hypothesis_id=args.hypothesis_id,
            budget_override=used_override,
        )
    except ProbeValidationError as exc:
        # Nothing ran, so the slot is refunded: a typo does not burn the run's budget.
        budget.used = max(0, budget.used - 1)
        _save_state(args.run_id, budget, cache)
        return {"error": "probe_validation_error", "detail": str(exc)}, True
    cache.put(key, result)
    _save_state(args.run_id, budget, cache)
    _publish_probe_event(build_probe_event(result, session_id=args.run_id, planning_mode=args.planning_mode))
    return result.model_dump(mode="json"), False


def _probe_budget(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.models.probe import ProbeBudgetStatus

    budget, _ = _load_state(args.run_id, args.planning_mode)
    status = ProbeBudgetStatus(
        used=budget.used,
        remaining=budget.remaining,
        total=budget.total,
        planning_mode=budget.planning_mode,
        by_hypothesis_id=dict(budget.by_hypothesis_id),
        by_mode={budget.planning_mode: budget.used},
    )
    return status.model_dump(mode="json"), False


def run_probe(args: argparse.Namespace) -> None:
    """Dispatch ``probe run|budget``."""
    handler = {"run": _probe_run, "budget": _probe_budget}.get(str(args.probe_command))
    if handler is None:
        print("usage: trw-mcp probe {run|budget}", file=sys.stderr)
        sys.exit(2)
    document, failed = handler(args)
    _emit(document, as_json=args.as_json, failed=failed)


def _propose(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.meta_tune import promote
    from trw_mcp.models.config import get_config

    content = args.candidate_content
    if args.candidate_file is not None:
        content = Path(args.candidate_file).read_text(encoding="utf-8")
    approval_ts = datetime.fromisoformat(args.approval_ts.replace("Z", "+00:00")) if args.approval_ts else None
    result = promote.promote_candidate(
        target_path=Path(args.target_path),
        candidate_content=content,
        proposer_id=args.proposer_id,
        reviewer_id=args.reviewer_id,
        approval_ts=approval_ts,
        sandbox_command=shlex.split(args.sandbox_command),
        declared_metric_delta=args.declared_metric_delta,
        promotion_session_id=args.promotion_session_id,
        state_dir=Path(args.state_dir) if args.state_dir is not None else None,
        _config=get_config(),
    )
    return result.model_dump(), False


def _rollback(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    from trw_mcp.meta_tune.rollback import rollback_proposal
    from trw_mcp.models.config import get_config

    config = get_config()
    # Only the audit log path may be redirected; a disabled subsystem stays
    # disabled, and rollback_proposal reports that itself (SAFE-001 FR-7, FR-13).
    if args.audit_log_path is not None:
        config = config.model_copy(
            update={"meta_tune": config.meta_tune.model_copy(update={"audit_log_path": args.audit_log_path})}
        )
    state_dir = Path(args.state_dir) if args.state_dir is not None else None
    result = rollback_proposal(args.proposal_id, state_dir=state_dir, _config=config)
    return result.model_dump(), result.status == "error"


def run_meta_tune(args: argparse.Namespace) -> None:
    """Dispatch ``meta-tune propose|rollback``."""
    handler = {"propose": _propose, "rollback": _rollback}.get(str(args.meta_tune_command))
    if handler is None:
        print("usage: trw-mcp meta-tune {propose|rollback}", file=sys.stderr)
        sys.exit(2)
    document, failed = handler(args)
    _emit(document, as_json=args.as_json, failed=failed)
