"""Run-level trust evidence collection — PRD-CORE-206 FR04 enforce path.

Belongs to the ``state/trust.py`` facade. Re-exported there.

Bridges persisted PRD-CORE-205 typed receipts to the outcome-consumption
primitives in ``_trust_outcome.py``. A receipt reaches this layer only after the
build/verification gate validated its plan coverage at write time; here we
re-verify that the bound content is still *current* and that the recorded outcome
is a *pass* before treating a kind as positive evidence. Review receipts and
acceptable-failure records are never collected — they are never eligible kinds.

Freshness re-verification matters: a receipt that was a pass when written is stale
if any bound file changed afterward, and a stale receipt is not positive evidence
(FR04 "current binding" column).
"""

from __future__ import annotations

import hashlib
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
    """A verification receipt is positive iff its outcome is PASS and content is current."""
    if receipt.outcome is not VerificationOutcome.PASS:
        return False
    return _binding_current(receipt.content_binding, project_root)


def collect_positive_trust_evidence(
    run_path: Path,
    project_root: Path,
) -> tuple[set[str], list[tuple[str, str]]]:
    """Return (positive receipt kinds, contributing (receipt_id, canonical_digest) pairs).

    Fail-toward-no-evidence: an unreadable/malformed/stale receipt is skipped, never
    counted. The canonical digest is the SHA-256 of the persisted canonical bytes so
    the receipt-set digest binds the exact evidence consumed.
    """
    from trw_mcp.state._evidence_persistence import list_receipt_ids, read_receipt_bytes

    positive_kinds: set[str] = set()
    contributing: list[tuple[str, str]] = []

    for receipt_id in list_receipt_ids(run_path, "build"):
        raw = read_receipt_bytes(run_path, "build", receipt_id)
        if raw is None:
            continue
        try:
            build = BuildReceipt.model_validate_json(raw)
            plan_path = run_path / "meta" / "plans" / "validation" / f"{build.plan_id}.json"
            plan = RequiredValidationPlan.model_validate_json(plan_path.read_bytes())
        except Exception:  # justified: a malformed persisted receipt is non-positive, never a crash
            logger.warning("trust_build_receipt_unparsable", receipt_id=receipt_id)
            continue
        if _build_receipt_is_positive(build, plan, project_root):
            positive_kinds.add("build")
            contributing.append((build.receipt_id, hashlib.sha256(raw).hexdigest()))

    for receipt_id in list_receipt_ids(run_path, "verification"):
        raw = read_receipt_bytes(run_path, "verification", receipt_id)
        if raw is None:
            continue
        try:
            verification = VerificationReceipt.model_validate_json(raw)
        except Exception:  # justified: a malformed persisted receipt is non-positive, never a crash
            logger.warning("trust_verification_receipt_unparsable", receipt_id=receipt_id)
            continue
        if _verification_receipt_is_positive(verification, project_root):
            positive_kinds.add("verification")
            contributing.append((verification.receipt_id, hashlib.sha256(raw).hexdigest()))

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
