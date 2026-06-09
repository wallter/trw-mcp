"""Deliver-tool step helpers — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Three step helpers used by ``trw_deliver``:

- ``unpack_gate_result`` — copy delivery-gate verdict keys to the typed
  result dict.
- ``step_clear_score`` — compute + persist PRD-HPO-MEAS-001 FR-5 CLEAR
  score for the closing session.
- ``log_deliver_complete`` — emit deliver_ok / deliver_failed /
  trw_deliver_complete log lines.

Extracted as DIST-243 batch 64 to push parent ``ceremony.py`` away from
the 717-LOC top-of-list violator position.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.state._helpers import read_jsonl_resilient

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import DeliverResultDict, DeliveryGatesDict

logger = structlog.get_logger(__name__)

_GATE_KEYS: tuple[str, ...] = (
    "review_block",
    "review_warning",
    "review_advisory",
    "integration_review_block",
    "integration_review_warning",
    "untracked_warning",
    "build_gate_warning",
    "build_gate_block",
    "build_gate_override",
    "warning",
    "review_scope_block",
    "checkpoint_blocker_warning",
    "complexity_drift_warning",
)


def evaluate_blocking_gates(
    *,
    gate_result: DeliveryGatesDict,
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    allow_unverified: bool,
    unverified_reason: str,
    events: object,
) -> bool:
    """Apply the deliver blocking-gate cascade. Return True to short-circuit.

    Encapsulates the integration-review / review-scope / review-block /
    task-type deliver-gate / build-gate cascade extracted from ``trw_deliver``
    (BEHAVIOR-PRESERVING). Mutates ``results``/``errors`` in place exactly as
    the inline cascade did. Returns ``True`` when delivery must stop (the
    caller returns ``results`` immediately), ``False`` to proceed.

    ``events`` is the module-level ``FileEventLogger`` (passed in so the
    monkeypatched ``ceremony._events`` stays authoritative).
    """
    # Block delivery if integration review has blocking verdict
    if gate_result.get("integration_review_block"):
        errors.append(str(gate_result["integration_review_block"]))
        results["errors"] = errors
        results["success"] = False
        return True

    # Block delivery if >5 files modified without review (R-01)
    if gate_result.get("review_scope_block"):
        errors.append(str(gate_result["review_scope_block"]))
        results["errors"] = errors
        results["success"] = False
        return True

    # Block delivery when the review verdict is 'block' with critical findings
    # on a STANDARD/COMPREHENSIVE run. This is the primary truthfulness gate:
    # a block review must actually block (CONSTITUTION §1). The sanctioned
    # escape hatch is allow_unverified + a concrete unverified_reason
    # (Deliver Gate Path 3) — honored exactly like the build/integration gates.
    review_block = gate_result.get("review_block")
    if review_block and not (allow_unverified and unverified_reason.strip()):
        errors.append(str(review_block))
        results["errors"] = errors
        results["success"] = False
        logger.warning("deliver_review_block", run=str(resolved_run))
        return True
    if review_block:
        # Override taken — make the bypass of the truthfulness gate UNMISSABLE,
        # mirroring the build-gate override audit trail (A-P1-02).
        reason = unverified_reason.strip()
        results["truthfulness_gate_bypassed"] = reason
        logger.warning(
            "review_block_override_used",
            reason=reason,
            review_block=str(review_block),
            run=str(resolved_run),
        )
        if resolved_run is not None and (resolved_run / "meta").exists():
            events.log_event(  # type: ignore[attr-defined]
                resolved_run / "meta" / "events.jsonl",
                "delivery_gate_overridden",
                {"reason": reason, "review_block": str(review_block)},
            )

    # PRD-CORE-184-FR03: task-type-aware deliver gate. When the configured
    # deliver_gate_mode + the run's task_type promote the advisory build
    # gate to a structural block, treat the missing build check as a hard
    # gate (still overridable via allow_unverified + unverified_reason).
    # When the mode is advisory (default) ``delivery_blocked`` is never set,
    # so this is a pure no-op for existing deployments (zero regression).
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

    # PRD-DIST-1865 / iter-29 Track-A: do not let "must call deliver"
    # override truthfulness.  A run with work events but no successful
    # trw_build_check can still be delivered through an explicit
    # acceptable-failure override, but not silently.
    build_gate_warning = gate_result.get("build_gate_warning")
    if build_gate_warning:
        reason = unverified_reason.strip()
        if not allow_unverified or not reason:
            block = (
                f"Delivery blocked: {build_gate_warning} "
                "If this is an acceptable failure, retry with "
                "allow_unverified=true and a concrete unverified_reason."
            )
            results["build_gate_block"] = block
            errors.append(block)
            results["errors"] = errors
            results["success"] = False
            return True
        results["build_gate_override"] = reason
        # A-P1-02: the truthfulness gate (CONSTITUTION §1.a) was bypassed via
        # allow_unverified. Previously this was only stored in a result key —
        # invisible to an operator watching the log/event stream, and
        # indistinguishable from a legitimate acceptable-failure deliver.
        # Make the bypass UNMISSABLE: a WARNING, a prominent result key, and
        # (when a run exists) a delivery_gate_overridden event for audit.
        results["truthfulness_gate_bypassed"] = reason
        logger.warning(
            "build_gate_override_used",
            reason=reason,
            build_gate_warning=str(build_gate_warning),
            run=str(resolved_run),
        )
        if resolved_run is not None and (resolved_run / "meta").exists():
            events.log_event(  # type: ignore[attr-defined]
                resolved_run / "meta" / "events.jsonl",
                "delivery_gate_overridden",
                {"reason": reason, "build_gate_warning": str(build_gate_warning)},
            )
    return False


def unpack_gate_result(gate_result: DeliveryGatesDict, results: DeliverResultDict) -> None:
    """Copy delivery-gate verdict keys from ``gate_result`` to ``results``.

    Each key in :data:`_GATE_KEYS` is conditionally promoted onto the typed
    result dict so callers see only populated fields.
    """
    for key in _GATE_KEYS:
        if key in gate_result:
            results[key] = gate_result[key]  # type: ignore[literal-required]


def step_clear_score(resolved_run: Path, results: DeliverResultDict) -> None:
    """PRD-HPO-MEAS-001 FR-5 — compute + persist CLEAR score for the run.

    One record per closed session; failure is fail-open so the scorer
    never blocks deliver completion.
    """
    try:
        from trw_mcp.scoring.clear import load_and_score_run

        session_id = str(resolved_run.name)
        clear_score = load_and_score_run(session_id, resolved_run)
        if clear_score is None:
            return
        clear_path = resolved_run / "meta" / "session_clear_score.json"
        clear_path.write_text(
            json.dumps(clear_score.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        results["clear_score"] = cast("dict[str, object]", clear_score.model_dump(mode="json"))
        logger.info(
            "clear_score_persisted",
            session_id=session_id,
            cost=clear_score.cost,
            latency=clear_score.latency,
            efficacy=clear_score.efficacy,
            assurance=clear_score.assurance,
            reliability=clear_score.reliability,
        )
    except Exception:  # justified: fail-open — CLEAR scoring must not block deliver
        logger.debug("clear_score_step_failed", exc_info=True)


def step_knowledge_sync(trw_dir: Path, results: DeliverResultDict) -> None:
    """PRD-FIX-COMPOUNDING-2 FR03 — auto-trigger knowledge-graph topic sync.

    After the core deliver logic succeeds, populate ``.trw/knowledge/`` from
    graph data when the entry count meets ``knowledge_sync_threshold``.
    ``execute_knowledge_sync`` already short-circuits below threshold, so the
    result is surfaced under the ``knowledge_sync`` key either way. Fail-open
    (NFR02): a sync failure must NOT fail the deliver — it is recorded as
    ``{"status": "failed", ...}`` instead.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.knowledge_topology import execute_knowledge_sync

        sync_result = execute_knowledge_sync(trw_dir, get_config(), dry_run=False)
        results["knowledge_sync"] = sync_result
    except Exception as exc:  # justified: fail-open — knowledge sync must not block deliver
        logger.warning("deliver_knowledge_sync_failed", error=str(exc), exc_info=True)
        results["knowledge_sync"] = {"status": "failed", "error": str(exc)}


def log_deliver_complete(
    *,
    resolved_run: Path | None,
    results: DeliverResultDict,
    errors: list[str],
    deferred_status: str,
    critical_elapsed: float,
) -> None:
    """Emit deliver_ok / deliver_failed / trw_deliver_complete log lines.

    Reads events.jsonl from the run dir for the events_logged field when
    available; missing/unreadable counts fall back to 0.
    """
    run_id = str(resolved_run.name) if resolved_run else ""
    events_jsonl = resolved_run / "meta" / "events.jsonl" if resolved_run else None
    # events.jsonl is read here only for the advisory events_logged count on the
    # deliver_ok line. The strict reader raises StateError on a torn concurrent
    # append, which would abort deliver-completion logging and break the
    # docstring's "unreadable counts fall back to 0" contract; the resilient
    # reader honors it by dropping the torn line (returns [] when missing).
    events_logged = len(read_jsonl_resilient(events_jsonl)) if events_jsonl else 0
    if not errors:
        logger.info(
            "deliver_ok",
            run_id=run_id,
            task=str(results.get("run_path", "")),
            events_logged=events_logged,
        )
    else:
        logger.warning("deliver_failed", run_id=run_id, errors=errors)
    if deferred_status == "skipped_already_running":
        logger.warning("deliver_deferred", reason="background_thread_running")
    logger.info(
        "trw_deliver_complete",
        critical_steps=results.get("critical_steps_completed"),
        deferred=deferred_status,
        critical_elapsed=critical_elapsed,
        errors=len(errors),
    )
