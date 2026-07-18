"""Single-table deliver-gate dispatch.

Belongs to the ``_ceremony_deliver_tool.py`` facade (reached at runtime via the
``ceremony.py`` registration module). This module collapses the deliver gate's
formerly near-identical block/return branches into ONE descriptor table
(:data:`_GATE_TABLE`) iterated once, with per-``OverridePolicy`` handling.

Precedence (must not change — this IS the framework's central truthfulness
enforcement order): NO_ESCAPE  →  review_block  →  delivery_blocked  →
build_gate_warning. The three gate policies are:

- ``NO_ESCAPE``   — ``integration_review_block`` / ``review_scope_block``.
  Objective, machine-checkable hard gates (INFRA-027 verdict==block; R-01
  >5 files modified with no review). Deliberately HARDER than a human review
  block: there is no ``allow_unverified`` escape — the remedy is to run the
  missing review, not assert an acceptable failure. This is a hardening ABOVE
  CONSTITUTION §1.a Deliver-Gate Path 3, not an oversight.
  # trw:intentional no allow_unverified escape — hardened above CONSTITUTION §1.a Path 3
  A co-firing ``review_block`` message is SURFACED into ``errors`` but NOT
  routed through its override here: delivery is already unconditionally blocked
  by the non-overridable gate so the structured escape would be moot (F4 fix —
  pre-fix this returned on the first match and silently dropped a co-firing
  ``review_block``, misattributing the cause across two deliver attempts).

- ``STRUCTURED`` — ``review_block`` (CORE-192 escalation) and
  ``delivery_blocked`` (deliver_gate_mode). HARD blocks overridable ONLY via a
  structured, auditable PRD-CORE-191 ``AcceptableFailureRecord``
  (``allow_unverified=true`` + a JSON ``unverified_reason`` carrying
  failed_command/residual_risk/owner/expiry_iso). Free text is REJECTED here —
  a hard build block can only be sanctioned by the structured record.

- ``ADVISORY`` — the SOFT ``build_gate_warning`` that survives ``deliver_gate_mode``
  + task-type dispatch (PRD-CORE-184). ``check_delivery_gates`` promotes a
  missing-build warning to the hard ``delivery_blocked`` key ONLY for the
  build-bearing task types (coding/rca/eval under ``block_coding`` or
  ``block_all``). Advisory task classes remain advisory under both policies. A
  ``build_gate_warning`` that REMAINS after that dispatch is
  intentionally advisory (docs/research/planning/unknown, or explicit advisory
  mode, or an unpinned session): it is surfaced but NEVER promoted back into a
  block. ``delivery_blocked`` is the hard promotion of the same condition, so
  when it is present the ADVISORY phase is a no-op.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.gate_decision import (
    DeliveryDecisionSet,
    GateDecision,
    GateOverridePolicy,
    GateStatus,
)
from trw_mcp.models.typed_dicts import DeliverResultDict

logger = structlog.get_logger(__name__)


class OverridePolicy(str, Enum):
    """How a fired gate may (or may not) be overridden."""

    NO_ESCAPE = "no_escape"
    STRUCTURED = "structured"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class GateDescriptor:
    """One deliver gate: its ``gate_result`` key, override policy, and result keys."""

    key: str
    policy: OverridePolicy
    result_block_key: str
    gate_type: str


# Precedence order is load-bearing — see module docstring. Iterated top-to-bottom.
_GATE_TABLE: tuple[GateDescriptor, ...] = (
    GateDescriptor(
        "integration_review_block", OverridePolicy.NO_ESCAPE, "integration_review_block", "integration_review_block"
    ),
    GateDescriptor("review_scope_block", OverridePolicy.NO_ESCAPE, "review_scope_block", "review_scope_block"),
    GateDescriptor("review_block", OverridePolicy.STRUCTURED, "review_block", "review_block"),
    GateDescriptor("delivery_blocked", OverridePolicy.STRUCTURED, "delivery_blocked", "delivery_blocked"),
    GateDescriptor("build_gate_warning", OverridePolicy.ADVISORY, "build_gate_warning", "build_gate_warning"),
)


def evaluate_delivery_gates(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    """Run the deliver-gate cascade once. Returns True iff delivery must BLOCK.

    Iterates :data:`_GATE_TABLE` in precedence order, dispatching each fired gate
    by its :class:`OverridePolicy`. A valid structured override lets a hard gate
    pass and evaluation continues to the next gate; a remaining soft warning is
    advisory and never blocks.
    """
    # CORE-205 FR07/FR08: typed decisions are now the authoritative dispatch
    # input.  The projector preserves the stable public keys while eliminating
    # parallel handwritten interpretations of gate policy.
    from trw_mcp.models.gate_decision import gate_decision_enabled

    if not gate_decision_enabled():
        raise RuntimeError("GateDecision dispatch requires the v26.1 receipt closure")
    decision_set = _build_decision_set(gate_result)
    _persist_decision_set(resolved_run, decision_set)
    typed_gate_result: Mapping[str, object] = decision_set.project_public_keys()

    if _evaluate_no_escape(typed_gate_result, results, errors):
        return True
    if _evaluate_structured(
        typed_gate_result, results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason
    ):
        return True
    # PRD-CORE-213-FR04/FR05: acceptance-integrity transition gate. Runs after the
    # existing STRUCTURED gates and shares their PRD-CORE-191 override contract. It
    # self-computes (path-limited PRD diff + coherence) rather than reading a
    # gate_result key, so it is NOT a _GATE_TABLE descriptor. Fail-open: any
    # resolution error degrades to no-block (NFR02).
    if _evaluate_acceptance_integrity(results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason):
        return True
    return _evaluate_advisory(typed_gate_result, resolved_run)


def _build_decision_set(gate_result: Mapping[str, object]) -> DeliveryDecisionSet:
    """Translate fired gate keys once into the strict decision model."""
    decisions: list[GateDecision] = []
    task_type = str(gate_result.get("blocked_task_type", "unknown"))
    missing_gate = str(gate_result.get("missing_gate", "build_check"))
    for descriptor in _GATE_TABLE:
        message = gate_result.get(descriptor.key)
        if not message:
            continue
        policy = GateOverridePolicy(descriptor.policy.value)
        status = GateStatus.WARN if descriptor.policy is OverridePolicy.ADVISORY else GateStatus.BLOCK
        decisions.append(
            GateDecision(
                decision_id=f"gate-{uuid.uuid4().hex}",
                gate_id=descriptor.key,
                status=status,
                override_policy=policy,
                reason_code=descriptor.gate_type,
                message=str(message),
                task_type=task_type,
                missing_evidence=(missing_gate,) if descriptor.key == "delivery_blocked" else (),
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )
        )
    return DeliveryDecisionSet(decisions=tuple(decisions))


def _persist_decision_set(resolved_run: Path | None, decision_set: DeliveryDecisionSet) -> None:
    """Persist every evaluated decision as immutable audit evidence."""
    if resolved_run is None:
        return
    from trw_mcp.state.persistence import FileStateWriter

    writer = FileStateWriter()
    for decision in decision_set.decisions:
        path = resolved_run / "meta" / "decisions" / f"{decision.decision_id}.json"
        writer.write_text(path, decision.model_dump_json(exclude_none=True) + "\n")


def _evaluate_acceptance_integrity(
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


def _emit_block(
    results: DeliverResultDict,
    errors: list[str],
    *,
    result_block_key: str,
    block_value: str,
    error_message: str,
) -> None:
    """The single block body shared by every overridable-but-not-overridden gate."""
    results[result_block_key] = block_value  # type: ignore[literal-required]
    errors.append(error_message)
    results["errors"] = errors
    results["success"] = False


def _evaluate_no_escape(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
) -> bool:
    """NO_ESCAPE phase — hard-block on integration-review / review-scope gates.

    No ``allow_unverified`` escape by design.
    # trw:intentional no allow_unverified escape — hardened above CONSTITUTION §1.a Path 3
    """
    no_escape_keys = tuple(d.key for d in _GATE_TABLE if d.policy is OverridePolicy.NO_ESCAPE)
    if not any(gate_result.get(key) for key in no_escape_keys):
        return False
    # A non-overridable hard gate fired — collect EVERY active hard-block message
    # (incl. a co-firing human ``review_block``) so none is silently dropped. The
    # block keys are already promoted onto ``results`` by ``unpack_gate_result``;
    # here we only surface their messages into the errors list the agent reads. A
    # co-firing ``review_block`` is NOT routed through its override — delivery is
    # already unconditionally blocked, so the structured escape would be moot.
    for key in ("integration_review_block", "review_scope_block", "review_block"):
        message = gate_result.get(key)
        if message:
            errors.append(str(message))
    results["errors"] = errors
    results["success"] = False
    return True


def _evaluate_structured(
    gate_result: Mapping[str, object],
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    """STRUCTURED phase — review_block then delivery_blocked, each hard-gated behind
    a PRD-CORE-191 acceptable-failure record. A valid record lets a gate pass and
    evaluation continues to the next STRUCTURED gate."""
    for desc in _GATE_TABLE:
        if desc.policy is not OverridePolicy.STRUCTURED:
            continue
        block_reason = gate_result.get(desc.key)
        if not block_reason:
            continue
        if desc.key == "delivery_blocked":
            results["missing_gate"] = str(gate_result.get("missing_gate", "build_check"))
            results["blocked_task_type"] = str(gate_result.get("blocked_task_type", "unknown"))
        if _hard_block_override(
            results=results,
            errors=errors,
            resolved_run=resolved_run,
            trw_dir=trw_dir,
            allow_unverified=allow_unverified,
            unverified_reason=unverified_reason,
            block_reason=str(block_reason),
            gate_type=desc.gate_type,
            result_block_key=desc.result_block_key,
        ):
            if desc.key == "delivery_blocked":
                logger.warning(
                    "deliver_gate_mode_blocked",
                    task_type=str(gate_result.get("blocked_task_type", "unknown")),
                    run=str(resolved_run),
                )
            return True
    return False


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
    False when a structurally valid, unexpired record lets delivery proceed. Free
    text is NOT accepted here — ``apply_structured_override`` rejects prose.
    """
    from trw_mcp.tools._acceptable_failure_validation import apply_structured_override

    if not (allow_unverified and unverified_reason.strip()):
        _emit_block(
            results, errors, result_block_key=result_block_key, block_value=block_reason, error_message=block_reason
        )
        logger.warning("deliver_hard_block", gate_type=gate_type, run=str(resolved_run))
        return True

    proceed, error = apply_structured_override(
        results=cast("dict[str, object]", results),
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        unverified_reason=unverified_reason,
        gate_type=gate_type,
    )
    if not proceed:
        # Override attempted but the record was prose / missing fields / expired:
        # the hard block stands (results[key]=block_reason, but the errors entry is
        # the specific validation error the agent must act on).
        _emit_block(
            results, errors, result_block_key=result_block_key, block_value=block_reason, error_message=str(error)
        )
        return True
    _log_gate_override(resolved_run, {"gate_type": gate_type, "block": block_reason})
    return False


def _evaluate_advisory(
    gate_result: Mapping[str, object],
    resolved_run: Path | None,
) -> bool:
    """ADVISORY phase — a build_gate_warning that survived deliver_gate_mode.

    ``check_delivery_gates`` promotes missing-build evidence to the hard
    ``delivery_blocked`` key only when the configured policy + task type require a
    block. A warning that remains after that dispatch is intentionally advisory
    and is surfaced but never promoted back into a block. Always returns False.
    """
    if gate_result.get("delivery_blocked"):
        return False
    build_gate_warning = gate_result.get("build_gate_warning")
    if not build_gate_warning:
        return False
    logger.info(
        "deliver_build_gate_advisory",
        build_gate_warning=str(build_gate_warning),
        run=str(resolved_run),
    )
    return False


def _log_gate_override(resolved_run: Path | None, payload: dict[str, object]) -> None:
    if resolved_run is None or not (resolved_run / "meta").exists():
        return
    from trw_mcp.tools import ceremony as _ceremony

    _ceremony._events.log_event(resolved_run / "meta" / "events.jsonl", "delivery_gate_overridden", payload)
