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
    step_session_changelog,
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

    # PRD-QUAL-042-FR02 (path-traversal): a caller-supplied ``run_path`` must
    # resolve INSIDE the project root. Without this an explicit run_path like
    # ``../../etc`` would make deliver checkpoint/copy artifacts into arbitrary
    # directories outside the project. Mirrors the containment check in
    # ``_paths.resolve_run_path``. Reject (do not silently fall back) so the
    # traversal attempt is visible.
    if run_path and resolved_run is not None:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root().resolve()
        if not resolved_run.is_relative_to(project_root):
            logger.warning(
                "deliver_run_path_escapes_project",
                run_path=str(resolved_run),
                project_root=str(project_root),
            )
            results["run_path"] = None
            block_msg = f"run_path escapes project root: {resolved_run}"
            errors.append(block_msg)
            results["errors"] = errors
            results["delivery_blocked"] = block_msg
            results["success"] = False
            return results

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
        gate_result, results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason
    ):
        return results
    if _block_or_record_missing_build(
        gate_result, results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason
    ):
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
    # PRD-LOCAL-049: durable session changelog artifact. Only runs with an
    # active run dir; fail-open inside the step so it never blocks deliver.
    if resolved_run is not None:
        step_session_changelog(resolved_run, results)

    deferred_status = _launch_deferred(trw_dir, resolved_run, results_view, skip_index_sync=skip_index_sync)
    results["deferred"] = deferred_status
    results["errors"] = errors
    results["success"] = len(errors) == 0
    results["critical_steps_completed"] = 2 - len(errors)
    results["deferred_steps"] = 11
    _ceremony._aggregate_advisory_warnings(results)
    _mark_deliver_and_reflect_learning(trw_dir, results)
    _write_nudge_analysis_artifact(trw_dir, results)
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
    """Hard-block delivery on integration-review / review-scope gate failures.

    F1 rationale (deliver-gate governance review lane) — WHY there is no
    ``allow_unverified`` escape here, unlike the human ``review_block`` (handled
    downstream by ``_block_or_record_review_override`` via the structured
    acceptable-failure path of CONSTITUTION §1.a Deliver Gate Path 3):

    ``integration_review_block`` and ``review_scope_block`` are deliberately
    HARDER than a human review-block. They fire on objective, machine-checkable
    facts — an integration-review.yaml verdict of ``block`` (INFRA-027), or
    >5 files modified with NO review at all (R-01). There is no judgment call to
    sanction away: the remedy is to run the missing review, not to assert an
    acceptable failure. Withholding the override path is a deliberate hardening
    ABOVE Constitution §1.a Path 3, not an oversight. Do NOT add an override
    branch here without an explicit governance decision to soften the gate.
    # trw:intentional no allow_unverified escape — hardened above CONSTITUTION §1.a Path 3

    F4 fix (deliver-gate governance review lane) — when one of these
    non-overridable gates fires, collect ALL active hard-block messages
    (integration_review_block, review_scope_block, AND a simultaneously-active
    human review_block) into ``errors`` before returning. Pre-fix this returned
    on the FIRST match, so a co-firing ``review_block`` was silently dropped:
    the agent would fix the surfaced gate, retry, and only then discover the
    hidden second block — misattributing the cause across two deliver attempts.

    A co-firing ``review_block`` is surfaced (errors + ``results['review_block']``)
    but NOT routed through its override handler here: delivery is already and
    unconditionally blocked by the non-overridable gate, so the structured
    acceptable-failure escape would be moot. Once the agent clears the hard gate,
    the next deliver attempt routes ``review_block`` through
    ``_block_or_record_review_override`` as normal.
    """
    non_overridable_keys = ("integration_review_block", "review_scope_block")
    if not any(gate_result.get(key) for key in non_overridable_keys):
        return False
    # A non-overridable hard gate fired — collect EVERY active hard-block message
    # so a co-firing review_block is not silently dropped. The block keys are
    # already promoted onto ``results`` by ``unpack_gate_result``; here we only
    # surface their messages into the errors list the agent reads.
    for key in ("integration_review_block", "review_scope_block", "review_block"):
        message = gate_result.get(key)
        if message:
            errors.append(str(message))
    results["errors"] = errors
    results["success"] = False
    return True


def _hard_block_override(
    *,
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
    block_reason: str,
    gate_type: str,
    result_block_key: str,
) -> bool:
    """PRD-CORE-191 — gate a HARD block behind a structured acceptable-failure record.

    Returns True when delivery must be BLOCKED (no/invalid/expired override),
    False when a structurally valid, unexpired record lets delivery proceed.
    Used for both ``review_block`` (CORE-192 escalation) and ``delivery_blocked``
    (deliver_gate_mode). The structured record is REQUIRED here — a hard block
    can only be overridden via the auditable acceptable-failure path.
    """
    from trw_mcp.tools._acceptable_failure_validation import apply_structured_override

    if not (allow_unverified and unverified_reason.strip()):
        results[result_block_key] = block_reason  # type: ignore[literal-required]
        errors.append(block_reason)
        results["errors"] = errors
        results["success"] = False
        logger.warning("deliver_hard_block", gate_type=gate_type, run=str(resolved_run))
        return True

    results_view = cast("dict[str, object]", results)
    proceed, error = apply_structured_override(
        results=results_view,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        unverified_reason=unverified_reason,
        gate_type=gate_type,
    )
    if not proceed:
        # Override attempted but the record was prose / missing fields / expired:
        # the hard block stands.
        results[result_block_key] = block_reason  # type: ignore[literal-required]
        errors.append(str(error))
        results["errors"] = errors
        results["success"] = False
        return True
    _log_gate_override(resolved_run, {"gate_type": gate_type, "block": block_reason})
    return False


def _block_or_record_review_override(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    review_block = gate_result.get("review_block")
    if not review_block:
        return False
    return _hard_block_override(
        results=results,
        errors=errors,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        allow_unverified=allow_unverified,
        unverified_reason=unverified_reason,
        block_reason=str(review_block),
        gate_type="review_block",
        result_block_key="review_block",
    )


def _block_or_record_missing_build(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    delivery_blocked = gate_result.get("delivery_blocked")
    if delivery_blocked:
        results["missing_gate"] = str(gate_result.get("missing_gate", "build_check"))
        if _hard_block_override(
            results=results,
            errors=errors,
            resolved_run=resolved_run,
            trw_dir=trw_dir,
            allow_unverified=allow_unverified,
            unverified_reason=unverified_reason,
            block_reason=str(delivery_blocked),
            gate_type="delivery_blocked",
            result_block_key="delivery_blocked",
        ):
            logger.warning(
                "deliver_gate_mode_blocked",
                task_type=str(gate_result.get("blocked_task_type", "unknown")),
                run=str(resolved_run),
            )
            return True
        return False

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
    # PRD-CORE-191-FR05: the SOFT build_gate_warning override remains
    # backward-compatible with a free-text reason — but if the reason parses as a
    # structured AcceptableFailureRecord we record + ledger it; otherwise we honor
    # the free-text bypass and emit a deprecation advisory pointing at the schema.
    # codex cross-model review (REFUTE/DOCUMENT): free-text on a SOFT build warning
    # is the SANCTIONED FR05 deprecation path (a graceful migration window), not an
    # un-gated bypass — FRAMEWORK.md §Deliver Gate Path 3 permits a documented
    # acceptable-failure, and the advisory below actively steers callers to the
    # structured schema. The HARD blocks (review_block / delivery_blocked) do NOT
    # accept free text — they require the structured record via _hard_block_override.
    from trw_mcp.tools._acceptable_failure_validation import apply_structured_override, parse_acceptable_failure

    record, _parse_error = parse_acceptable_failure(reason)
    if record is not None:
        apply_structured_override(
            results=cast("dict[str, object]", results),
            resolved_run=resolved_run,
            trw_dir=trw_dir,
            unverified_reason=reason,
            gate_type="build_gate_warning",
        )
        results["build_gate_override"] = reason
    else:
        results["build_gate_override"] = reason
        results["truthfulness_gate_bypassed"] = reason
        results["acceptable_failure_advisory"] = (
            "Free-text unverified_reason is deprecated. Provide a structured "
            "acceptable-failure record (failed_command, residual_risk, owner, expiry_iso) "
            "as JSON so the override is auditable. See PRD-CORE-191."
        )
        _log_gate_override(resolved_run, {"reason": reason, "build_gate_warning": str(build_gate_warning)})
    logger.warning(
        "build_gate_override_used", reason=reason, build_gate_warning=str(build_gate_warning), run=str(resolved_run)
    )
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


def _write_nudge_analysis_artifact(trw_dir: Path, results: DeliverResultDict) -> None:
    """Write the live nudge-effectiveness artifact and surface a compact summary.

    Nudge-deep-dive work target #1/#2: computes responsiveness, per-step
    resistance, recall-pull, and timing-validity from this session's own
    ceremony-state + surface stream, writes ``.trw/context/nudge-analysis.json``,
    and attaches a summary (incl. flagged resistance steps) to the deliver
    result so an operator/loop can read the structural signal directly.

    Runs AFTER ``mark_deliver`` so the deliver step counts toward responsiveness.
    Fail-open: analysis is observability and must never block deliver.
    """
    try:
        from trw_mcp.state._session_id import resolve_effective_session_id
        from trw_mcp.state.nudge_analysis import (
            analysis_summary,
            compute_nudge_analysis,
            persist_nudge_analysis,
        )

        session_id = resolve_effective_session_id(trw_dir)
        analysis = compute_nudge_analysis(trw_dir, session_id=session_id)
        path = persist_nudge_analysis(trw_dir, analysis)
        if path is not None:
            summary = analysis_summary(analysis)
            summary["artifact"] = str(path)
            results["nudge_analysis"] = summary
    except Exception:  # justified: fail-open — nudge analysis must not block deliver
        logger.debug("nudge_analysis_artifact_failed", exc_info=True)


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
