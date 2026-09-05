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
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import DeliverResultDict

logger = structlog.get_logger(__name__)

__all__ = ["evaluate_acceptance_integrity", "evaluate_formation", "evaluate_plan_acceptance"]


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
    except Exception:  # justified: gate resolution failure degrades to no-block (NFR02)
        logger.warning("acceptance_integrity_dispatch_degraded", run=str(resolved_run), exc_info=True)
        return False
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
    try:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._plan_acceptance_gate import evaluate_plan_acceptance

        run_yaml = resolved_run / "meta" / "run.yaml"
        run_data = FileStateReader().read_yaml(run_yaml) if run_yaml.is_file() else {}
        outcome = evaluate_plan_acceptance(resolved_run, run_data)
    except Exception:  # justified: run-state resolution failure degrades to no-block (NFR02)
        logger.warning("plan_acceptance_dispatch_degraded", run=str(resolved_run), exc_info=True)
        return False
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
) -> bool:
    """PRD-CORE-265-FR11 — block ORCHESTRATOR delivery on a non-terminal member.

    An orchestrator can pass every single-run gate and deliver while three peers
    are mid-implementation; that is a completion claim outrunning its evidence,
    which the value hierarchy ranks above velocity. Fires only on the run that
    OWNS the manifest, so a member delivering on its own is unaffected and the
    gate cannot deadlock a formation by blocking the members it waits for.

    Returns True when delivery must BLOCK. The only escape is the same
    PRD-CORE-191 acceptable-failure record every hard gate honours — free text is
    rejected by ``apply_structured_override``, not by anything here.
    """
    from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate

    outcome = evaluate_formation_gate(resolved_run)
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
