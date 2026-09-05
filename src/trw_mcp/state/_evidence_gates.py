"""Shared receipt validators + observe/enforce mode reader — CORE-205 FR02-FR08.

Relocated from ``tools/_evidence_gates.py`` (PRD-FIX-061-FR07): receipt
validation is a state-layer concern consumed by ``state/_trust_receipts.py``,
and the state layer must never import from ``tools/``. The old module path
remains as a re-export shim.

Every gate reader (review, build, verification, delivery) consumes ONE
:class:`ReceiptValidationResult` from these validators instead of interpreting
mode-specific dicts. The validators derive substance/outcome from validated
content — a naked ``substantive`` boolean, artifact existence, clean verdict, or
arbitrary passing command never upgrades a non-``VALID`` state (FR03/NFR01).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models._evidence_core import (
    EvidenceMode,
    ReceiptState,
    ReceiptValidationResult,
)
from trw_mcp.models._evidence_plans import RequiredReviewPlan, RequiredValidationPlan
from trw_mcp.models._evidence_records import BuildReceipt, ReviewReceipt, VerificationReceipt
from trw_mcp.state._evidence_binding import content_binding_is_current

logger = structlog.get_logger(__name__)

#: Stable machine reason code for a receipt that aged out of its TTL window.
#: Deliberately distinct from the content-binding check's ``bound_content_changed``
#: so logs and tests can tell "the reviewed bytes moved" apart from "the verdict
#: is simply too old" (PRD-CORE-255-FR01).
REVIEW_VERDICT_EXPIRED = "review_verdict_expired"

#: Remedy surfaced with every expiry. Names BOTH recovery paths: minting a fresh
#: receipt, and the stdio version-skew case where the running server predates the
#: check entirely (PRD-CORE-255-NFR03 S1).
REVIEW_VERDICT_EXPIRED_REMEDY = (
    "re-run trw_review to mint a fresh receipt within the TTL window "
    "(and /mcp reconnect when the server predates the field)"
)


def _resolve_review_ttl_hours() -> int | None:
    """The configured review-verdict TTL, or ``None`` when it cannot be read.

    ``None`` is the fail-CLOSED signal, not an error: the caller treats an
    unreadable tunable as "this receipt has expired" rather than as "this receipt
    never expires" (PRD-CORE-255-NFR01). Returning the field's default instead
    would silently substitute a policy the project may not have chosen.
    """
    try:
        from trw_mcp.models.config import get_config

        return int(get_config().review_verdict_ttl_hours)
    except Exception:  # justified: fail-CLOSED, an unreadable TTL must not grant unbounded validity
        logger.warning("review_verdict_ttl_read_failed", outcome="fail_closed", exc_info=True)
        return None


def review_receipt_age_seconds(completed_at: str, *, now: datetime | None = None) -> float | None:
    """Elapsed seconds since ``completed_at``, or ``None`` when it cannot be read.

    ONE parser for both consumers of a receipt's age — the FR01 expiry check and
    the FR05 ``review_evidence.age_seconds`` the deliver payload reports — so the
    two can never disagree about how old a receipt is.
    """
    try:
        stamped = datetime.fromisoformat(completed_at)
    except (TypeError, ValueError):
        return None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - stamped).total_seconds()


def review_verdict_is_expired(completed_at: str, *, now: datetime | None = None) -> bool:
    """True when ``completed_at`` is older than ``review_verdict_ttl_hours``.

    Independent of the content binding: a receipt whose bound bytes never moved
    still expires. An unreadable TTL or an unparseable/absent ``completed_at``
    is EXPIRED — the check can only ever fail toward no-evidence, so no input and
    no failure mode restores the pre-CORE-255 never-expires posture (FR01/NFR01).
    """
    ttl_hours = _resolve_review_ttl_hours()
    if ttl_hours is None:
        return True
    age = review_receipt_age_seconds(completed_at, now=now)
    if age is None:
        logger.warning("review_verdict_completed_at_unparseable", completed_at=completed_at, outcome="fail_closed")
        return True
    return age > ttl_hours * 3600


def _result(
    state: ReceiptState,
    reason: str,
    *,
    receipt_id: str | None = None,
    typed_present: bool = True,
    diagnostics: str = "",
) -> ReceiptValidationResult:
    return ReceiptValidationResult(
        state=state,
        reason_code=reason,
        receipt_id=receipt_id,
        typed_present=typed_present,
        diagnostics=diagnostics,
    )


def validate_review_receipt(
    receipt: ReviewReceipt,
    plan: RequiredReviewPlan,
    project_root: Path,
) -> ReceiptValidationResult:
    """Derive substantive review evidence from a current receipt (FR02/FR03).

    A clean zero-finding receipt is substantive only when its schema, provenance,
    current binding, realized method, verdict, and one realized result for every
    required plan item validate and it is not degraded.
    """
    rid = receipt.receipt_id
    if receipt.is_degraded:
        return _result(ReceiptState.DEGRADED, "review_degraded", receipt_id=rid)
    if receipt.review_plan_digest != plan.plan_digest:
        return _result(ReceiptState.INVALID, "review_plan_digest_mismatch", receipt_id=rid)
    if receipt.review_input_digest != receipt.expected_input_digest(plan.governing_content_digest):
        return _result(ReceiptState.INVALID, "review_input_digest_mismatch", receipt_id=rid)
    if not receipt.covers_plan(plan.required_rubric_ids, plan.required_reviewer_roles):
        return _result(ReceiptState.PLAN_INCOMPLETE, "review_plan_incomplete", receipt_id=rid)
    if not receipt.is_structurally_substantive(plan.required_rubric_ids, plan.required_reviewer_roles):
        return _result(ReceiptState.INVALID, "review_not_substantive", receipt_id=rid)
    freshness = content_binding_is_current(receipt.content_binding, project_root)
    if freshness.state is not ReceiptState.VALID:
        return _result(freshness.state, freshness.reason_code, receipt_id=rid)
    # PRD-CORE-255-FR01: the TIME axis, evaluated after (and independently of)
    # the content binding. Both must pass; neither can substitute for the other.
    if review_verdict_is_expired(receipt.completed_at):
        logger.warning(
            "review_verdict_expired",
            receipt=rid,
            completed_at=receipt.completed_at,
            remedy=REVIEW_VERDICT_EXPIRED_REMEDY,
        )
        return _result(ReceiptState.STALE_CONTENT, REVIEW_VERDICT_EXPIRED, receipt_id=rid)
    return _result(ReceiptState.VALID, "review_substantive", receipt_id=rid)


def validate_build_receipt(
    receipt: BuildReceipt,
    plan: RequiredValidationPlan,
    project_root: Path,
) -> ReceiptValidationResult:
    """Derive a build pass from complete plan coverage + current content (FR04/FR05).

    Pass requires an exact result for every required plan command, all required
    exits zero, thresholds met, no contradictory legacy boolean, and current
    bound content. Timestamp ordering is never a substitute for a content match.
    """
    rid = receipt.receipt_id
    if receipt.plan_digest != plan.plan_digest:
        return _result(ReceiptState.INVALID, "build_plan_digest_mismatch", receipt_id=rid)
    if not receipt.covers_required(plan.required_command_ids):
        return _result(ReceiptState.PLAN_INCOMPLETE, "build_plan_incomplete", receipt_id=rid)
    derived = receipt.derived_outcome(plan.required_command_ids, plan.coverage_threshold)
    if receipt.legacy_contradicts_outcome(derived):
        return _result(ReceiptState.INVALID, "build_legacy_contradiction", receipt_id=rid)
    if not derived:
        return _result(ReceiptState.INVALID, "build_required_command_failed", receipt_id=rid)
    freshness = content_binding_is_current(receipt.content_binding, project_root)
    if freshness.state is not ReceiptState.VALID:
        return _result(freshness.state, freshness.reason_code, receipt_id=rid)
    return _result(ReceiptState.VALID, "build_pass_current", receipt_id=rid)


def validate_verification_receipt(
    receipt: VerificationReceipt,
    current_mapping_digest: str,
    project_root: Path,
) -> ReceiptValidationResult:
    """Validate executed verification evidence against its mapping snapshot (FR06).

    A changed mapping does not silently reuse the receipt — it is reported against
    the old mapping digest (``stale_mapping``) rather than as current evidence.
    Persisting/validating never mutates any PRD lifecycle field.
    """
    rid = receipt.receipt_id
    if not receipt.matches_mapping(current_mapping_digest):
        return _result(ReceiptState.STALE_CONTENT, "verification_mapping_changed", receipt_id=rid)
    freshness = content_binding_is_current(receipt.content_binding, project_root)
    if freshness.state is not ReceiptState.VALID:
        return _result(freshness.state, freshness.reason_code, receipt_id=rid)
    return _result(ReceiptState.VALID, "verification_recorded", receipt_id=rid)


def read_evidence_mode(config: object) -> EvidenceMode | None:
    """Resolve the configured observe/enforce mode; unknown value -> None (FR08).

    An unknown mode or a config-read failure is non-positive: the caller treats
    ``None`` as "cannot trust typed evidence policy" rather than silently
    defaulting to a permissive path.
    """
    try:
        raw = str(getattr(config, "evidence_receipt_mode", "observe"))
    except Exception:  # justified: config attribute resolution failure is non-positive (FR08)
        logger.warning("evidence_mode_read_failed", exc_info=True)
        return None
    try:
        return EvidenceMode(raw)
    except ValueError:
        logger.warning("evidence_mode_unknown", mode=raw)
        return None


def select_typed_review_state(
    run_path: Path | None,
) -> ReceiptValidationResult:
    """FR03 artifact selection: distinguish typed_absent from typed_present.

    Returns a ``LEGACY_UNBOUND`` (``typed_present=False``) result when NO typed
    review receipt exists — the reader MAY then consult the legacy projection in
    observe mode. When a typed receipt path exists but cannot be loaded/validated,
    returns a non-positive ``typed_present=True`` result and the reader SHALL NOT
    fall back to legacy.
    """
    from trw_mcp.state._evidence_persistence import list_receipt_ids, read_receipt_bytes

    if run_path is None:
        return _result(ReceiptState.LEGACY_UNBOUND, "no_run_pin", typed_present=False)
    ids = list_receipt_ids(run_path, "review")
    if not ids:
        return _result(ReceiptState.LEGACY_UNBOUND, "typed_absent", typed_present=False)
    latest = ids[-1]
    raw = read_receipt_bytes(run_path, "review", latest)
    if raw is None:
        return _result(ReceiptState.INVALID, "typed_present_unreadable", receipt_id=latest)
    try:
        ReviewReceipt.model_validate_json(raw)
    except Exception:  # justified: a present-but-malformed typed receipt is non-positive, never legacy fallback
        return _result(ReceiptState.INVALID, "typed_present_malformed", receipt_id=latest)
    # A structurally valid receipt exists; full current-content validation is the
    # caller's responsibility (it must supply the server-resolved plan). Presence
    # alone is reported so the reader never consults a legacy projection.
    return _result(ReceiptState.VALID, "typed_present_loadable", receipt_id=latest)
