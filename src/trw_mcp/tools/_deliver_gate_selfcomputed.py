"""Deliver gates that COMPUTE their own condition (PRD-CORE-213, PRD-CORE-249).

Split out of ``_deliver_gate_dispatch`` 2026-09-04. Behaviour-preserving move:
the two functions below are byte-identical to the ones the dispatcher used to
hold, and the dispatcher calls them in the same order, at the same point, with
the same arguments.

WHY THEY ARE A GROUP. Every descriptor in ``_GATE_TABLE`` fires by reading a key
another module already put in ``gate_result``. These do not: each opens the run's
own artifacts, decides for itself, and then hands the verdict to the dispatcher's
shared ``_hard_block_override`` so the PRD-CORE-191 acceptable-failure contract
stays in exactly one place. That is a different KIND of gate, and keeping the two
kinds in one file is what pushed the dispatcher past the module-size gate.

``_hard_block_override`` is imported lazily inside each function: the dispatcher
imports this module, so a module-level import back into it would be a cycle.

A FAULT IS NEVER A PASS (E2E-DELIVER-GATE-DISPATCH-FAIL-CLOSED; CONSTITUTION §1.a
outranks the old "degrade to no-block" NFR02 clauses). An unexpected exception
while a gate computes its condition goes to :func:`_fault_verdict`: it BLOCKS
(structured, so an acceptable-failure record still releases it) unless that
gate's own mode is warn/advisory, where it is a NAMED warning -- never a new hard
block there, never silent. The verdict is set before any logging.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import DeliverResultDict

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

logger = structlog.get_logger(__name__)

__all__ = [
    "evaluate_acceptance_integrity",
    "evaluate_build_authority",
    "evaluate_formation",
    "evaluate_plan_acceptance",
    "log_unused_override_intent",
]


def evaluate_acceptance_integrity(
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    """PRD-CORE-213 — block an incoherent PRD status->implemented transition.

    Detects a ``->implemented`` transition in this session's path-limited PRD diff
    and, under ``prd_transition_gate=block`` + a build-bearing task type, requires
    functionality_level coherence, wiring/behavioral evidence, build evidence, and
    an independent P0/P1 review receipt. A shortfall is a STRUCTURED hard block
    overridable ONLY via a PRD-CORE-191 acceptable-failure record. Returns True
    when delivery must BLOCK. No active run or a warn-mode / clean transition =>
    False (never a spurious block).
    """
    if resolved_run is None:
        return False
    try:
        from trw_mcp.tools._prd_transition_gate import evaluate_transition_gate

        outcome = evaluate_transition_gate(resolved_run)
    except Exception as exc:  # justified: a fault is never a pass -- blocks unless warn/advisory (CORE-213-NFR02)
        return _fault_verdict(
            results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason, exc,
            gate="PRD-CORE-213 transition", gate_type="acceptance_integrity",
            result_block_key="acceptance_integrity_block", warning_key="acceptance_integrity_warning",
            enforced=_transition_gate_enforced(),
        )  # fmt: skip
    # Surface a non-blocking advisory so the delivering agent SEES it (mirrors the
    # build_gate_warning idiom; no dormant warn path). Present whenever the gate
    # found non-certifying items that did not hard-block.
    if outcome.warning:
        results["acceptance_integrity_warning"] = outcome.warning  # type: ignore[typeddict-unknown-key]
        logger.info("acceptance_integrity_advisory", run=str(resolved_run), warning=outcome.warning)
    # PRD-QUAL-119-FR06: surface the universal typed completion outcome per PRD
    # so the delivering agent consumes decision vocabulary, not just token lists.
    if outcome.decision_outcomes:
        results["effective_completion_outcomes"] = dict(outcome.decision_outcomes)  # type: ignore[typeddict-unknown-key]
    if not outcome.should_block:
        return False
    from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override

    return _hard_block_override(
        results=results,
        errors=errors,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        allow_unverified=allow_unverified,
        unverified_reason=unverified_reason,
        block_reason=outcome.message,
        gate_type="acceptance_integrity",
        result_block_key="acceptance_integrity_block",
    )


def evaluate_plan_acceptance(
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    """PRD-CORE-249-FR04 — block delivery over an unaddressed acceptance identifier.

    Enumerates the governing acceptance identifiers (anchored ``P-``/``X-``/``AC-``
    in ``reports/plan.md`` plus ``FR\\d+`` from the FR headings of every PRD named
    in ``prd_scope``) and reads the run's ``reports/acceptance.yaml`` declaration.
    Blocks when the resolved mode is ``block_coding``/``block_all``, the task type
    carries a build artifact, at least one identifier was enumerated, and at least
    one is ``unaddressed`` or ``blocked:automatable`` — a STRUCTURED hard block
    overridable only via a PRD-CORE-191 acceptable-failure record.

    ``unresolved_scope_entries`` is surfaced whenever a ``prd_scope`` entry names
    no resolvable PRD, so "zero enumerated identifiers" is never reported as a
    pass on a non-empty scope. Returns True when delivery must BLOCK; no active
    run, nothing enumerated, or an advisory mode => False.
    """
    if resolved_run is None:
        return False
    task_type = "unknown"
    stage = "reading meta/run.yaml"
    try:
        from trw_mcp.state._evidence_bound_read import read_evidence_mapping
        from trw_mcp.tools._plan_acceptance_gate import evaluate_plan_acceptance

        # Evidence-bound: absent is {} (as before); invalid, null or symlinked is a fault, never a skipped gate.
        run_data = read_evidence_mapping(resolved_run, "meta/run.yaml") or {}
        task_type = str(run_data.get("task_type") or "unknown")
        stage = "evaluating"
        outcome = evaluate_plan_acceptance(resolved_run, run_data)
    except Exception as exc:  # justified: a fault is never a pass -- blocks unless advisory (CORE-249-NFR02)
        return _fault_verdict(
            results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason, exc,
            gate=f"PRD-CORE-249 plan-acceptance ({stage})", gate_type="plan_acceptance",
            result_block_key="plan_acceptance_block", warning_key="plan_acceptance_warning",
            enforced=_deliver_mode_enforced(task_type),
        )  # fmt: skip
    if outcome.unresolved_scope_entries:
        results["unresolved_scope_entries"] = list(outcome.unresolved_scope_entries)
    if outcome.warning:
        results["plan_acceptance_warning"] = outcome.warning
    if not outcome.should_block:
        return False
    from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override

    return _hard_block_override(
        results=results,
        errors=errors,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        allow_unverified=allow_unverified,
        unverified_reason=unverified_reason,
        block_reason=outcome.message,
        gate_type="plan_acceptance",
        result_block_key="plan_acceptance_block",
    )


def evaluate_formation(
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
    *,
    call_ctx: TRWCallContext | None = None,
) -> bool:
    """PRD-CORE-265-FR11 — block ORCHESTRATOR delivery on a non-terminal member.

    An orchestrator can pass every single-run gate and deliver while three peers
    are mid-implementation; that is a completion claim outrunning its evidence,
    which the value hierarchy ranks above velocity. Fires only on the run that
    OWNS the manifest, so a member delivering on its own is unaffected and the
    gate cannot deadlock a formation by blocking the members it waits for.

    *call_ctx* is the calling session's own resolved identity, threaded through
    to :func:`_formation_deliver_gate.evaluate_formation_gate` so its R1 caller
    verification (PRD-FIX-149 review) can tell an orchestrator's own delivery
    from a peer merely naming the orchestrator's ``run_path``.

    Returns True when delivery must BLOCK. The only escape is the same
    PRD-CORE-191 acceptable-failure record every hard gate honours — free text is
    rejected by ``apply_structured_override``, not by anything here.
    """
    from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate

    outcome = evaluate_formation_gate(resolved_run, call_ctx=call_ctx)
    if outcome.warning:
        results["formation_gate_warning"] = outcome.warning
    if not outcome.should_block:
        return False
    return _hard_block_override(
        results=results,
        errors=errors,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        allow_unverified=allow_unverified,
        unverified_reason=unverified_reason,
        block_reason=outcome.message,
        gate_type="formation_member_incomplete",
        result_block_key="formation_gate_block",
    )


def log_unused_override_intent(allow_unverified: bool, unverified_reason: str, resolved_run: Path | None) -> None:
    """Record that an override was OFFERED to a delivery no gate blocked — PRD-FIX-140-FR02.

    The bundled hook used to append a line to ``.trw/context/deliver-override-audit.jsonl``
    whenever override intent arrived on its advisory path, because the server
    never saw that case: a structured record is only validated when a STRUCTURED
    gate actually fires. Demoting the hook (FR01) would have dropped that
    observation entirely, so it is emitted here instead — the caller asserted an
    acceptable failure for a delivery that had nothing to accept.
    """
    if not (allow_unverified and unverified_reason.strip()):
        return
    logger.info(
        "deliver_override_intent_unused",
        run=str(resolved_run) if resolved_run else "",
        reason_chars=len(unverified_reason.strip()),
    )


def evaluate_build_authority(
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    """PRD-FIX-140-FR04/FR05 — the build rules the client-side hook used to own.

    Two conditions, both previously enforced ONLY by
    ``data/hooks/pre-tool-deliver-gate.sh`` and therefore invisible to any client
    that does not run it:

    * FR04 — an UNPINNED delivery with no passing build record for its own session.
      ``check_delivery_gates`` returns before the gate-mode dispatch for
      ``run_path=None``, so the decision is made here, against the SAME predicate
      and the same change-evidence clause the pinned path uses: a recorded build
      FAILURE blocks, and so does a session that recorded modifications to at
      least ``deliver_gate_unclassified_change_threshold`` distinct files with no
      passing build. A session with no recorded modifications stays advisory, so
      the docs-only over-block this PRD exists to fix does not return. Unreadable
      evidence — of the build result OR of the change count — blocks.
    * FR05 — a pinned run whose MOST RECENT build check reported failure. The
      build gate is satisfied by any earlier passing event, so a pass-then-fail
      run could deliver on stale good news.

    Both are STRUCTURED blocks: a PRD-CORE-191 acceptable-failure record still
    releases them, so the gate can force evidence or a record but never wedge a
    session. Both fail CLOSED on uncomputable evidence, and so does an unexpected
    fault (PRD-FIX-140-FR10): a pinned run blocks whatever the mode, as
    FR05 does; an unpinned one blocks unless its mode is advisory.
    """
    try:
        if resolved_run is None:
            from trw_mcp.state._paths import get_session_id, resolve_pin_key
            from trw_mcp.tools._deliver_gate_mode import resolve_unpinned_gate_decision
            from trw_mcp.tools._delivery_event_checks import (
                PROCESS_STARTED_AT,
                unpinned_build_failure_recorded,
                unpinned_build_passed,
                unpinned_session_changed_files,
            )

            # resolve_pin_key, NOT get_session_id: the two evidence WRITERS key on
            # the pin key — trw_build_check persists its receipt under
            # ``resolve_pin_key(ctx=ctx)`` (tools/build/_registration.py) and the
            # PostToolUse hook writes its unpinned change record under
            # ``trw_pin_key`` (lib-trw.sh), which is the same TRW_SESSION_ID-first
            # ladder. Reading with the process UUID instead would miss both records
            # whenever TRW_SESSION_ID is set — i.e. exactly when hooks are wired —
            # and the gate would silently measure "nothing recorded".
            session_id = resolve_pin_key(None)
            # Only claude-code exports an identifier the shell hook and this
            # server both see. When the ladder bottomed out at the process UUID
            # the hook's records carry a key this process can never match, so the
            # change evidence is read unscoped: every unpinned record since this
            # server started (PRD-FIX-140-FR04, release-verify 2026-09-17 P1-2).
            key_is_unshared = session_id == get_session_id()
            recorded_failure = unpinned_build_failure_recorded(trw_dir, session_id)
            if recorded_failure is False and unpinned_build_passed(
                trw_dir, session_id, unscoped_since=PROCESS_STARTED_AT if key_is_unshared else None
            ):
                return False  # this session recorded a PASSING build after its last edit: satisfied
            files_changed = (
                None
                if recorded_failure is not False
                else unpinned_session_changed_files(
                    trw_dir, session_id, unscoped_since=PROCESS_STARTED_AT if key_is_unshared else None
                )
            )
            blocked, mode = resolve_unpinned_gate_decision(files_changed)
            if not blocked:
                return False
            if recorded_failure:
                cause = "the last trw_build_check recorded by this session reported a failure"
            elif recorded_failure is None:
                cause = "this session's ceremony state could not be read, so its build result is unknown"
            elif files_changed is None:
                cause = _uncomputable_cause(session_id, trw_dir)
            elif key_is_unshared:
                cause = (
                    f"{files_changed} file(s) were modified by unpinned sessions in this checkout since this "
                    "server started (this client publishes no session id a hook can share, so the evidence "
                    "is read unscoped) and no passing trw_build_check was recorded"
                )
            else:
                cause = f"this session recorded modifications to {files_changed} file(s) and no passing trw_build_check"
            reason = (
                f"Delivery blocked: {cause}, and the session is not pinned to a run "
                f"(deliver_gate_mode={mode}). "
                "Run project-native validation and record it with trw_build_check(), call "
                "trw_init() / `trw-mcp run adopt` so run-scoped evidence can be checked, or override with "
                "allow_unverified=true + an unexpired acceptable-failure record."
            )
        else:
            from trw_mcp.tools._delivery_event_checks import latest_build_check_failed_for_run

            latest_failed = latest_build_check_failed_for_run(resolved_run)
            if latest_failed is False:
                return False
            reason = (
                "Delivery blocked: the most recent trw_build_check in this run reported a failure"
                if latest_failed
                else "Delivery blocked: this run's event log could not be read, so the latest build result is unknown"
            ) + (
                ". Fix the failures, re-run project-native validation and record the new result with "
                "trw_build_check(), or override with allow_unverified=true + an unexpired "
                "acceptable-failure record."
            )
    except Exception as exc:  # justified: a fault is never a pass (PRD-FIX-140-FR10)
        blocked = _fault_verdict(
            results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason, exc,
            gate="PRD-FIX-140 build-authority", gate_type="delivery_blocked",
            result_block_key="delivery_blocked", warning_key="build_authority_warning",
            enforced=resolved_run is not None or _deliver_mode_enforced("unknown"),
        )  # fmt: skip
        if blocked:
            results["missing_gate"] = "build_check"
        return blocked
    from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override

    blocked_delivery = _hard_block_override(
        results=results,
        errors=errors,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        allow_unverified=allow_unverified,
        unverified_reason=unverified_reason,
        block_reason=reason,
        gate_type="delivery_blocked",
        result_block_key="delivery_blocked",
    )
    if blocked_delivery:
        results["missing_gate"] = "build_check"
    return blocked_delivery


def _transition_gate_enforced() -> bool:
    """Whether the CORE-213 gate enforces: ``prd_transition_gate`` is ``block`` and the deliver mode is not advisory."""
    try:
        from trw_mcp.models.config import get_config

        transition = str(get_config().prd_transition_gate)
    except Exception:  # justified: unreadable config -> the field's declared default ("block"), as WD-05
        transition = "block"
    return transition == "block" and _deliver_mode_enforced("unknown")


def _deliver_mode_enforced(task_type: str) -> bool:
    try:
        from trw_mcp.tools._deliver_gate_mode import resolve_gate_mode

        return resolve_gate_mode(task_type) != "advisory"
    except Exception:  # justified: an unresolvable mode is not permission to pass -- enforced
        return True


def _fault_verdict(
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
    exc: Exception,
    *,
    gate: str,
    gate_type: str,
    result_block_key: str,
    warning_key: str,
    enforced: bool,
) -> bool:
    """A gate that could not compute its condition: block when *enforced*, else a named warning."""
    # The type name only: str(exc) runs arbitrary __str__ code and must not stand before the verdict.
    fault = f"the {gate} gate could not be evaluated ({type(exc).__name__})"
    if enforced:
        reason = (
            f"Delivery blocked: {fault}, so it cannot be shown to pass (deliver gates fail closed, "
            "CONSTITUTION §1.a). Fix the fault and re-run trw_deliver, or override with allow_unverified=true "
            "+ an unexpired acceptable-failure record."
        )
        # _hard_block_override writes the block (or, for an accepted acceptable-failure record, lets delivery
        # proceed); if it raises, the block is written below -- a failing override path is never a pass.
        try:
            from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override

            blocked = _hard_block_override(
                results=results, errors=errors, resolved_run=resolved_run, trw_dir=trw_dir,
                allow_unverified=allow_unverified, unverified_reason=unverified_reason, block_reason=reason,
                gate_type=gate_type, result_block_key=result_block_key,
            )  # fmt: skip
        except Exception:  # justified: the override path failed -- block, never a pass
            results[result_block_key] = reason  # type: ignore[literal-required]
            results["success"] = False
            if reason not in errors:
                errors.append(reason)
            results["errors"] = errors
            blocked = True
    else:
        cast("dict[str, object]", results)[warning_key] = f"{fault}; not enforced under the current gate mode"
        blocked = False
    try:
        logger.warning("deliver_gate_fault", gate=gate_type, blocked=blocked, run=str(resolved_run), exc_info=exc)
    except Exception:  # trw-fail-silent-allow: diagnostics after the verdict; a broken logger cannot change it
        pass
    return blocked


def _uncomputable_cause(session_id: str, trw_dir: Path) -> str:
    """Why this session's change count is unknown, naming what would resolve it."""
    from trw_mcp.client_profiles.change_evidence import active_client_writes_change_evidence, change_evidence_gap_reason
    from trw_mcp.state._session_change_witness import changed_since_snapshot

    witness = changed_since_snapshot(session_id, trw_dir.parent)
    if witness.status == "no_snapshot":  # a restart: never "re-run trw_session_start" (it would hide the edits)
        return witness.reason
    if not active_client_writes_change_evidence():
        return change_evidence_gap_reason()
    detail = "" if witness.status == "counted" else f" ({witness.reason})"
    return f"this session's change evidence could not be read, so it cannot be shown to be code-free{detail}"
