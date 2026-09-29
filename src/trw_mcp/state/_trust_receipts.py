"""Run-level trust evidence collection — PRD-CORE-206 FR04 enforce path.

Belongs to the ``state/trust.py`` facade. Re-exported there.

Bridges persisted PRD-CORE-205 typed receipts to the outcome-consumption
primitives in ``_trust_outcome.py``. Build receipts are revalidated against their
persisted plans. Verification receipts require current source and named artifact
bytes plus a PASS outcome; this does not authenticate their execution or prove
the currency of their mapping snapshot. Review receipts and
acceptable-failure records are never collected — they are never eligible kinds.

Freshness re-verification matters: a receipt that was a pass when written is stale
if any bound file changed afterward, and a stale receipt is not positive evidence
(FR04 "current binding" column).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import RequiredValidationPlan, VerificationOutcome
from trw_mcp.models._evidence_records import BuildReceipt, VerificationReceipt
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._trust_outcome import (
    TrustConsumeResult,
    TrustEligibility,
    classify_trust_eligibility,
    compute_receipt_set_digest,
    compute_trust_outcome_id,
    consume_trust_outcome,
)

logger = structlog.get_logger(__name__)


def _binding_current(content_binding: object, project_root: Path) -> bool:
    from trw_mcp.state._evidence_binding import content_binding_is_current

    outcome = content_binding_is_current(content_binding, project_root)  # type: ignore[arg-type]
    return outcome.state is ReceiptState.VALID


def _build_receipt_is_positive(
    receipt: BuildReceipt,
    plan: RequiredValidationPlan,
    project_root: Path,
) -> bool:
    """Revalidate the server plan and current binding before trust consumption."""
    from trw_mcp.state._evidence_gates import validate_build_receipt

    return validate_build_receipt(receipt, plan, project_root).is_positive


def _verification_receipt_is_positive(receipt: VerificationReceipt, project_root: Path) -> bool:
    """Require PASS plus current source and independently named artifact bytes.

    This collector has no authoritative current mapping snapshot; artifact
    freshness does not establish mapping currency or executor authentication.
    """
    from trw_mcp.state._verification_artifact import verification_artifact_is_current

    if receipt.outcome is not VerificationOutcome.PASS:
        return False
    return (
        _binding_current(receipt.content_binding, project_root)
        and verification_artifact_is_current(receipt, project_root).state is ReceiptState.VALID
    )


#: Receipt kinds the trust gate can count. Review receipts and acceptable-failure
#: records are never eligible (module docstring), so the classifier says so by name.
TRUST_ELIGIBLE_KINDS: tuple[str, ...] = ("build", "verification")

#: Reasons that mean the persisted bytes could not be read as a receipt (plus plan).
UNPARSABLE_REASONS: frozenset[str] = frozenset({"receipt_unparsable", "plan_unreadable"})

#: The gate's per-kind warning event names, unchanged from before the extraction.
_UNPARSABLE_EVENTS: dict[str, str] = {
    "build": "trust_build_receipt_unparsable",
    "verification": "trust_verification_receipt_unparsable",
}


@dataclass(frozen=True)
class ReceiptClassification:
    """One persisted receipt's standing under the trust gate's predicate, evaluated now.

    ``reason`` is one of ``positive``, ``not_positive``, ``receipt_unparsable``,
    ``plan_unreadable`` (build only) or ``kind_not_trust_eligible``. ``receipt`` is
    the parsed model when the bytes parsed, else ``None``.
    """

    receipt_type: str
    positive: bool
    reason: str
    receipt: BuildReceipt | VerificationReceipt | None = None


def classify_receipt(run_path: Path, receipt_type: str, raw: bytes, project_root: Path) -> ReceiptClassification:
    """Classify one receipt's raw bytes with the trust gate's per-receipt predicate.

    Runtime callers: :func:`collect_positive_trust_evidence` (the trust gate's loop)
    and ``trw_mcp.evidence_pack._evidence.evidence_section`` (PRD-CORE-323 FR03,
    ``positivity_at_export``), so the gate and the pack share one predicate.
    Soundness scope: the verdict is evaluated against the tree at call time; it
    says nothing about whether the receipt was positive when it was written or
    when ``trw_deliver`` read it. Fail-toward-no-evidence: every parse failure is
    non-positive and named, never raised.
    """
    if receipt_type not in TRUST_ELIGIBLE_KINDS:
        return ReceiptClassification(receipt_type, False, "kind_not_trust_eligible")
    if receipt_type == "build":
        try:
            build = BuildReceipt.model_validate_json(raw)
        except Exception:  # justified: a malformed persisted receipt is non-positive, never a crash
            return ReceiptClassification(receipt_type, False, "receipt_unparsable")
        try:
            plan_path = run_path / "meta" / "plans" / "validation" / f"{build.plan_id}.json"
            plan = RequiredValidationPlan.model_validate_json(plan_path.read_bytes())
        except Exception:  # justified: an unreadable plan leaves the receipt non-positive, never a crash
            return ReceiptClassification(receipt_type, False, "plan_unreadable", build)
        positive = _build_receipt_is_positive(build, plan, project_root)
        return ReceiptClassification(receipt_type, positive, "positive" if positive else "not_positive", build)
    try:
        verification = VerificationReceipt.model_validate_json(raw)
    except Exception:  # justified: a malformed persisted receipt is non-positive, never a crash
        return ReceiptClassification(receipt_type, False, "receipt_unparsable")
    positive = _verification_receipt_is_positive(verification, project_root)
    return ReceiptClassification(receipt_type, positive, "positive" if positive else "not_positive", verification)


def collect_positive_trust_evidence(
    run_path: Path,
    project_root: Path,
) -> tuple[set[str], list[tuple[str, str]]]:
    """Return (positive receipt kinds, contributing (receipt_id, canonical_digest) pairs).

    Fail-toward-no-evidence: an unreadable/malformed/stale receipt is skipped, never
    counted. The canonical digest is the SHA-256 of the persisted canonical bytes so
    the receipt-set digest binds the exact evidence consumed. Each receipt is judged
    by :func:`classify_receipt`, the predicate the evidence pack also reports.
    """
    from trw_mcp.state._evidence_persistence import list_receipt_ids, read_receipt_bytes

    positive_kinds: set[str] = set()
    contributing: list[tuple[str, str]] = []

    for receipt_type in TRUST_ELIGIBLE_KINDS:
        for receipt_id in list_receipt_ids(run_path, receipt_type):
            raw = read_receipt_bytes(run_path, receipt_type, receipt_id)
            if raw is None:
                continue
            verdict = classify_receipt(run_path, receipt_type, raw, project_root)
            if verdict.reason in UNPARSABLE_REASONS:
                logger.warning(_UNPARSABLE_EVENTS[receipt_type], receipt_id=receipt_id)
                continue
            if verdict.positive and verdict.receipt is not None:
                positive_kinds.add(receipt_type)
                contributing.append((verdict.receipt.receipt_id, hashlib.sha256(raw).hexdigest()))

    return positive_kinds, contributing


def evaluate_and_consume_trust_outcome(
    trw_dir: Path,
    run_path: Path | None,
    project_root: Path,
    task_type: str,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    config: TRWConfig | None = None,
) -> tuple[TrustEligibility, TrustConsumeResult | None]:
    """Collect current typed receipts, apply the closed matrix, consume once if eligible.

    Returns the eligibility verdict and, when eligible, the atomic consumption result.
    A non-eligible verdict returns ``(eligibility, None)`` and never touches the
    registry (FR04 / NFR01 fail-toward-no-increment).
    """
    if run_path is None:
        elig = classify_trust_eligibility(task_type, set())
        return elig, None

    positive_kinds, contributing = collect_positive_trust_evidence(run_path, project_root)
    eligibility = classify_trust_eligibility(task_type, positive_kinds)
    if not eligibility.eligible:
        return eligibility, None

    project_identity = project_root.resolve().name
    outcome_id = compute_trust_outcome_id(project_identity, run_path.name, session_id)
    receipt_set_digest = compute_receipt_set_digest(contributing)
    result = consume_trust_outcome(
        trw_dir,
        outcome_id,
        receipt_set_digest,
        agent_id=agent_id,
        config=config,
    )
    return eligibility, result
