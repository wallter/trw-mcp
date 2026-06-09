"""Implementation body for the ``trw_deliver`` ceremony tool.

The public FastMCP registration stays in :mod:`trw_mcp.tools.ceremony`; this
module owns the deliver workflow so the facade remains a small registration and
patch-seam module.  Runtime lookups intentionally go through the facade where
legacy tests/operators monkeypatch helpers such as ``resolve_trw_dir``,
``find_active_run``, ``_do_reflect``, ``_step_checkpoint``, and ``_events``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import Context

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DeliverResultDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_deliver_steps import (
    log_deliver_complete,
    step_clear_score,
    step_knowledge_sync,
    unpack_gate_result,
)
from trw_mcp.tools._deferred_delivery import _launch_deferred
from trw_mcp.tools._helpers import _run_step

logger = structlog.get_logger(__name__)


def run_trw_deliver(
    ctx: Context | None = None,
    run_path: str | None = None,
    skip_reflect: bool = False,
    skip_index_sync: bool = False,
    allow_unverified: bool = False,
    unverified_reason: str = "",
) -> DeliverResultDict:
    """Persist learnings/progress and launch deferred delivery housekeeping."""
    from trw_mcp.tools import ceremony as _ceremony

    get_config_fn = cast("Callable[[], TRWConfig]", vars(_ceremony)["get_config"])
    config = get_config_fn()
    reader = FileStateReader()
    writer = FileStateWriter()
    t0 = time.monotonic()
    results: DeliverResultDict = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    resolve_trw_dir_fn = cast("Callable[[], Path]", vars(_ceremony)["resolve_trw_dir"])
    trw_dir = resolve_trw_dir_fn()

    call_ctx = _ceremony._build_call_context(ctx)
    resolved_run = Path(run_path).resolve() if run_path else _ceremony._find_active_run_compat(call_ctx)
    results["run_path"] = str(resolved_run) if resolved_run else None
    candidate_runs = _ceremony._candidate_run_hints() if resolved_run is None else []
    if candidate_runs:
        results["candidate_runs"] = candidate_runs

    logger.info("deliver_started", run_id=str(resolved_run.name) if resolved_run else "", phase="DELIVER")

    from trw_mcp.models.run import Phase
    from trw_mcp.state.phase import try_update_phase
    from trw_mcp.tools._ceremony_helpers import check_delivery_gates, copy_compliance_artifacts

    try_update_phase(resolved_run, Phase.DELIVER)
    gate_result = check_delivery_gates(resolved_run, reader, trw_dir)
    unpack_gate_result(gate_result, results)

    compliance_result = copy_compliance_artifacts(resolved_run, trw_dir, config, reader, writer)
    if "compliance_artifacts_copied" in compliance_result:
        results["compliance_artifacts_copied"] = compliance_result["compliance_artifacts_copied"]
    if "compliance_dir" in compliance_result:
        results["compliance_dir"] = compliance_result["compliance_dir"]

    if _block_delivery_for_gate(gate_result, results, errors):
        return results
    if _block_or_record_review_override(
        gate_result, results, errors, resolved_run, allow_unverified, unverified_reason
    ):
        return results
    if _block_or_record_missing_build(gate_result, results, errors, resolved_run, allow_unverified, unverified_reason):
        return results

    results_view: dict[str, object] = cast("dict[str, object]", results)
    if not skip_reflect:
        _run_step("reflect", lambda: _ceremony._do_reflect(trw_dir, resolved_run), results_view, errors)
    else:
        results["reflect"] = {"status": "skipped"}

    if resolved_run is not None:
        _run_step("checkpoint", lambda: _ceremony._step_checkpoint(resolved_run), results_view, errors)
    else:
        checkpoint_skip: dict[str, object] = {
            "status": "skipped",
            "reason": "no_active_run",
            "detail": (
                "Learning persistence can still succeed, but run checkpointing was skipped because "
                "this MCP session has no pinned run."
            ),
            "hint": _ceremony._no_active_run_hint(candidate_runs),
        }
        if candidate_runs:
            checkpoint_skip["candidate_runs"] = candidate_runs
        results["checkpoint"] = checkpoint_skip

    results["claude_md_sync"] = {"status": "skipped", "reason": "PRD-CORE-093"}
    critical_elapsed = round(time.monotonic() - t0, 2)
    results["critical_elapsed_seconds"] = critical_elapsed

    _probe_integrity(trw_dir, resolved_run, results)
    if resolved_run is not None:
        step_clear_score(resolved_run, results)
    step_knowledge_sync(trw_dir, results)

    deferred_status = _launch_deferred(trw_dir, resolved_run, results_view, skip_index_sync=skip_index_sync)
    results["deferred"] = deferred_status
    results["errors"] = errors
    results["success"] = len(errors) == 0
    results["critical_steps_completed"] = 2 - len(errors)
    results["deferred_steps"] = 11
    _ceremony._aggregate_advisory_warnings(results)
    _mark_deliver_and_reflect_learning(trw_dir, results)
    _log_deliver_event(trw_dir, resolved_run, results, errors, deferred_status, critical_elapsed)
    log_deliver_complete(
        resolved_run=resolved_run,
        results=results,
        errors=errors,
        deferred_status=deferred_status,
        critical_elapsed=critical_elapsed,
    )
    return results


def _block_delivery_for_gate(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
) -> bool:
    for key in ("integration_review_block", "review_scope_block"):
        if gate_result.get(key):
            errors.append(str(gate_result[key]))
            results["errors"] = errors
            results["success"] = False
            return True
    return False


def _block_or_record_review_override(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    review_block = gate_result.get("review_block")
    if not review_block:
        return False
    reason = unverified_reason.strip()
    if not (allow_unverified and reason):
        errors.append(str(review_block))
        results["errors"] = errors
        results["success"] = False
        logger.warning("deliver_review_block", run=str(resolved_run))
        return True
    results["truthfulness_gate_bypassed"] = reason
    logger.warning("review_block_override_used", reason=reason, review_block=str(review_block), run=str(resolved_run))
    _log_gate_override(resolved_run, {"reason": reason, "review_block": str(review_block)})
    return False


def _block_or_record_missing_build(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    delivery_blocked = gate_result.get("delivery_blocked")
    if delivery_blocked and not (allow_unverified and unverified_reason.strip()):
        results["delivery_blocked"] = str(delivery_blocked)
        results["missing_gate"] = str(gate_result.get("missing_gate", "build_check"))
        errors.append(str(delivery_blocked))
        results["errors"] = errors
        results["success"] = False
        logger.warning(
            "deliver_gate_mode_blocked",
            task_type=str(gate_result.get("blocked_task_type", "unknown")),
            run=str(resolved_run),
        )
        return True

    build_gate_warning = gate_result.get("build_gate_warning")
    if not build_gate_warning:
        return False
    reason = unverified_reason.strip()
    if not allow_unverified or not reason:
        block = (
            f"Delivery blocked: {build_gate_warning} If this is an acceptable failure, retry with "
            "allow_unverified=true and a concrete unverified_reason."
        )
        results["build_gate_block"] = block
        errors.append(block)
        results["errors"] = errors
        results["success"] = False
        return True
    results["build_gate_override"] = reason
    results["truthfulness_gate_bypassed"] = reason
    logger.warning(
        "build_gate_override_used", reason=reason, build_gate_warning=str(build_gate_warning), run=str(resolved_run)
    )
    _log_gate_override(resolved_run, {"reason": reason, "build_gate_warning": str(build_gate_warning)})
    return False


def _log_gate_override(resolved_run: Path | None, payload: dict[str, object]) -> None:
    if resolved_run is None or not (resolved_run / "meta").exists():
        return
    from trw_mcp.tools import ceremony as _ceremony

    _ceremony._events.log_event(resolved_run / "meta" / "events.jsonl", "delivery_gate_overridden", payload)


def _probe_integrity(trw_dir: Path, resolved_run: Path | None, results: DeliverResultDict) -> None:
    try:
        from trw_mcp.tools._deliver_integrity import check_memory_integrity_on_deliver

        integrity_result = check_memory_integrity_on_deliver(trw_dir, resolved_run)
        results["db_integrity"] = cast("dict[str, object]", dict(integrity_result))
    except Exception:  # justified: fail-open — integrity probe must not block deliver
        logger.debug("deliver_integrity_check_failed", exc_info=True)


def _mark_deliver_and_reflect_learning(trw_dir: Path, results: DeliverResultDict) -> None:
    try:
        from trw_mcp.state.ceremony_progress import mark_deliver

        mark_deliver(trw_dir)
    except Exception:  # justified: fail-open — state mutation must not block deliver
        logger.debug("mark_deliver_failed", exc_info=True)
    try:
        from trw_mcp.state.ceremony_progress import read_ceremony_state
        from trw_mcp.tools import ceremony as _ceremony

        results["learning_reflection"] = _ceremony._learning_reflection_message(
            read_ceremony_state(trw_dir).learnings_this_session
        )
    except Exception:  # justified: fail-open — reflection must not block deliver
        logger.debug("learning_reflection_failed", exc_info=True)


def _log_deliver_event(
    trw_dir: Path,
    resolved_run: Path | None,
    results: DeliverResultDict,
    errors: list[str],
    deferred_status: str,
    critical_elapsed: float,
) -> None:
    if resolved_run is None or not (resolved_run / "meta").exists():
        return
    try:
        from trw_mcp.state.ceremony_progress import read_ceremony_state

        nudge_summary = dict(read_ceremony_state(trw_dir).nudge_counts)
    except Exception:  # justified: fail-open
        logger.debug("deliver_nudge_summary_unavailable", exc_info=True)
        nudge_summary = {}
    from trw_mcp.tools import ceremony as _ceremony

    _ceremony._events.log_event(
        resolved_run / "meta" / "events.jsonl",
        "trw_deliver_complete",
        {
            "critical_steps_completed": results.get("critical_steps_completed"),
            "deferred": deferred_status,
            "critical_elapsed_seconds": critical_elapsed,
            "errors": len(errors),
            "nudge_summary": nudge_summary,
        },
    )
