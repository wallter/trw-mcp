"""Canonical review-plan resolution and receipt persistence (CORE-205 FR02/FR03).

Review handlers pass their realized artifact through this module before the
legacy ``review.yaml`` projection is written.  The server, not the caller,
resolves the authoritative scope and the required rubric/role set.  A failure
to resolve that state produces no positive receipt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models._evidence_core import (
    ReceiptState,
    ReceiptValidationResult,
    ScopeConfidence,
    domain_digest,
)
from trw_mcp.models._evidence_plans import RequiredReviewPlan, ReviewVerdict
from trw_mcp.models._evidence_records import ReviewReceipt
from trw_mcp.models.run import ReviewFinding
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.state.review_signoffs import trw_dir_for_run
from trw_mcp.tools._evidence_binding import build_content_binding, mint_run_owned_scope
from trw_mcp.tools._evidence_gates import validate_review_receipt
from trw_mcp.tools._evidence_persistence import generate_receipt_id, read_receipt_bytes, write_receipt
from trw_mcp.tools._review_adversarial_source import adversarial_source_is_verified
from trw_mcp.tools._review_reviewer_family import resolve_reviewer_fields

logger = structlog.get_logger(__name__)

_POLICY_VERSION = "v26.1-review-receipts"
_MANUAL_RUBRICS: tuple[str, ...] = ("finding_schema_validation", "verdict_derivation")
_MANUAL_ROLES: tuple[str, ...] = ("manual_reviewer",)
_AUTO_RUBRICS: tuple[str, ...] = (
    "correctness",
    "security",
    "test-quality",
    "performance",
    "style",
    "spec-compliance",
)
_CROSS_MODEL_RUBRICS: tuple[str, ...] = ("cross_model_review",)
#: PRD-CORE-255-FR04. REALIZED (never required) — adding it to any plan's
#: required set would retroactively invalidate every ordinary review. It is
#: stamped only on a receipt whose reviewer posture is independently verified
#: (:func:`adversarial_source_is_verified`), so the rubric marks "an independent
#: adversarial pass was relayed through this receipt", not "the caller said so".
ADVERSARIAL_AUDIT_RUBRIC = "adversarial_audit"
_RUBRIC_POLICY = """TRW review receipt policy v26.1
manual: validate every finding and derive the verdict from normalized severity
auto: realize correctness, security, test-quality, performance, style, and spec-compliance roles
cross_model: require realized cross-family findings; same-family fallback uses the auto role set
limited or empty analysis is degraded and cannot be positive evidence
"""


@dataclass(frozen=True)
class ReviewReceiptWriteResult:
    """Outcome returned to the legacy projection and public review result."""

    receipt_id: str = ""
    plan_id: str = ""
    state: str = "missing"
    reason_code: str = "receipt_not_written"
    #: PRD-CORE-255-FR02: the family actually stamped, and — when a ``cross_model``
    #: claim was refused — which verification condition refused it. Carried back so
    #: the ``trw_review`` RESPONSE says so too; a structlog-only downgrade is
    #: invisible to the agent that made the claim.
    reviewer_family: str = ""
    family_downgraded_reason: str = ""
    #: True ONLY for a manual-mode family EARNED through a digest-verified external
    #: artifact. The in-process cross_model dispatch also stamps
    #: ``reviewer_family="cross_model"``, but its coverage is decided by whether the
    #: provider actually returned cross-family findings — that handler owns
    #: ``review_family_coverage`` and this must not overwrite its answer.
    verified_cross_model: bool = False

    @property
    def ok(self) -> bool:
        return self.state == "written" and bool(self.receipt_id)


def _governing_files(project_root: Path, prd_ids: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    """Bind exact governing PRD bytes plus the immutable review-policy bytes."""
    governed: list[dict[str, str]] = []
    paths: list[str] = []
    for prd_id in sorted(prd_ids):
        prds_dir = project_root / "docs" / "requirements-aare-f" / "prds"
        exact = prds_dir / f"{prd_id}.md"
        matches = [exact] if exact.is_file() else sorted(prds_dir.glob(f"{prd_id}-*.md"))
        if len(matches) != 1:
            raise ValueError(f"governing PRD {prd_id!r} did not resolve uniquely")
        path = matches[0]
        raw = path.read_bytes()
        relative = path.relative_to(project_root).as_posix()
        paths.append(relative)
        governed.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    digest = domain_digest(
        "review_governing_content",
        {
            "governed_files": governed,
            "rubric_policy_sha256": hashlib.sha256(_RUBRIC_POLICY.encode("utf-8")).hexdigest(),
            "policy_version": _POLICY_VERSION,
        },
    )
    return tuple(paths), digest


def _realized_roles(review_data: dict[str, object]) -> tuple[str, ...]:
    """Reviewer roles the artifact actually attests to, from its own stamp."""
    roles_raw = review_data.get("reviewer_roles_run", [])
    return tuple(str(role) for role in roles_raw) if isinstance(roles_raw, list) else ()


def _requirements_for_artifact(
    review_data: dict[str, object],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return required rubrics/roles and honestly realized rubrics/roles."""
    mode = str(review_data.get("mode", "manual") or "manual")
    substantive = review_data.get("substantive") is True
    if mode == "auto":
        required = _AUTO_RUBRICS
        # A non-substantive auto artifact realizes NOTHING, exactly like the
        # manual and cross_model branches below. Auto was the sole branch that
        # carried its realized roles through a degraded analysis.
        realized_roles = _realized_roles(review_data) if substantive else ()
        return required, required, realized_roles, realized_roles
    if mode == "cross_model":
        if str(review_data.get("review_family_coverage", "")) == "cross_family":
            realized = _CROSS_MODEL_RUBRICS if substantive else ()
            return _CROSS_MODEL_RUBRICS, _CROSS_MODEL_RUBRICS, realized, realized
        realized_roles = _realized_roles(review_data) if substantive else ()
        return _AUTO_RUBRICS, _AUTO_RUBRICS, realized_roles, realized_roles
    realized = _MANUAL_RUBRICS if substantive else ()
    realized_roles = _MANUAL_ROLES if substantive else ()
    return _MANUAL_RUBRICS, _MANUAL_ROLES, realized, realized_roles


def _validated_findings(review_data: dict[str, object]) -> tuple[ReviewFinding, ...]:
    raw = review_data.get("findings", review_data.get("cross_model_findings", []))
    if not isinstance(raw, list):
        return ()
    return tuple(ReviewFinding.model_validate(item) for item in raw if isinstance(item, dict))


def record_review_receipt(
    run_path: Path | None,
    review_data: dict[str, object],
    prd_ids: tuple[str, ...],
    *,
    policy_mode: str,
) -> ReviewReceiptWriteResult:
    """Resolve, persist, and validate the canonical plan-bound review receipt."""
    if run_path is None:
        return ReviewReceiptWriteResult(reason_code="no_run_pin")
    try:
        project_root = resolve_project_root()
        scope = mint_run_owned_scope(run_path, project_root, scope_id=f"review-{run_path.name}")
        if scope.confidence is ScopeConfidence.UNVERIFIABLE:
            return ReviewReceiptWriteResult(reason_code="scope_unverifiable")
        governing_paths, governing_digest = _governing_files(project_root, prd_ids)
        # Governing PRD bytes are server-added scope entries.  They never replace
        # or shrink the journal-derived required paths, and their mutation makes
        # the receipt stale through the same content-binding validator.
        scope = scope.model_copy(update={"proposed_paths": governing_paths})
        binding_outcome = build_content_binding(scope, project_root)
        if binding_outcome.binding is None:
            return ReviewReceiptWriteResult(reason_code=binding_outcome.reason_code)

        mode = str(review_data.get("mode", "manual") or "manual")
        reviewer = resolve_reviewer_fields(review_data, mode, project_root)
        required_rubrics, required_roles, realized_rubrics, realized_roles = _requirements_for_artifact(review_data)
        # PRD-CORE-255-FR04: the adversarial rubric is REALIZED (not required), so
        # it never changes plan coverage for an ordinary review — it only records
        # that this receipt relayed an independently-verified adversarial pass.
        substantive = review_data.get("substantive") is True
        review_id = str(review_data.get("review_id", ""))
        # The references an operator sign-off may be bound to. Both are decided
        # HERE, by the server, from state the caller cannot restate: the review's
        # own id and the scope digest the run's journal produced.
        review_refs = tuple(ref for ref in (review_id, scope.scope_digest) if ref)
        verification = adversarial_source_is_verified(
            reviewer, review_refs=review_refs, trw_dir=trw_dir_for_run(run_path)
        )
        adversarial_verified = substantive and verification.verified
        if adversarial_verified:
            realized_rubrics = (*realized_rubrics, ADVERSARIAL_AUDIT_RUBRIC)
        # A refused adversarial posture must SAY so. The family resolver's own
        # downgrade reason wins when it has one (it is the more specific fact);
        # otherwise the refusal reason is surfaced whenever the caller actually
        # staked something on the posture — an operator source, or an
        # adversarial_pass claim. Silence here is what let a self-minted operator
        # receipt look identical to a real one.
        downgraded_reason = reviewer.family_downgraded_reason
        staked_on_posture = reviewer.origin == "operator" or bool(review_data.get("adversarial_pass"))
        if not downgraded_reason and verification.reason and staked_on_posture:
            downgraded_reason = verification.reason
        plan_seed = {
            "plan_id": "pending",
            "scope_id": scope.scope_id,
            "scope_digest": scope.scope_digest,
            "governing_prd_ids": sorted(prd_ids),
            "governing_content_digest": governing_digest,
            "required_rubric_ids": sorted(required_rubrics),
            "required_reviewer_roles": sorted(required_roles),
            "policy_version": _POLICY_VERSION,
        }
        seed_digest = domain_digest("review_plan_identity", plan_seed)
        plan_id = f"review-plan-{seed_digest[:24]}"
        plan_digest = domain_digest("review_plan", {**plan_seed, "plan_id": plan_id})
        plan = RequiredReviewPlan(
            plan_id=plan_id,
            plan_digest=plan_digest,
            scope_id=scope.scope_id,
            scope_digest=scope.scope_digest,
            governing_prd_ids=prd_ids,
            governing_content_digest=governing_digest,
            required_rubric_ids=required_rubrics,
            required_reviewer_roles=required_roles,
            policy_version=_POLICY_VERSION,
        )
        plan_path = run_path / "meta" / "plans" / "review" / f"{plan_id}.json"
        FileStateWriter().write_text(plan_path, plan.model_dump_json(exclude_none=True) + "\n")

        receipt_id = generate_receipt_id("review")
        degraded_reason = ""
        if not substantive:
            degraded_reason = str(
                review_data.get("limited_reason")
                or review_data.get("non_substantive_reason")
                or "review_not_substantive"
            )
        raw_verdict = str(review_data.get("verdict", "")).strip()
        if not raw_verdict:
            # A receipt is authoritative evidence. Defaulting an absent verdict to
            # "pass" minted a PASS receipt for a review that never stated an
            # outcome — a gate reading it could not tell the two apart. No verdict
            # means no receipt, the same direction every other failure here takes.
            logger.warning("review_receipt_verdict_missing", run=str(run_path), review_plan_id=plan_id)
            return ReviewReceiptWriteResult(plan_id=plan_id, reason_code="review_verdict_missing")
        verdict = ReviewVerdict(raw_verdict)
        receipt = ReviewReceipt(
            receipt_id=receipt_id,
            review_id=review_id,
            run_id=run_path.name,
            completed_at=str(review_data.get("timestamp", "")),
            method=mode,
            reviewer_origin=reviewer.origin,
            reviewer_identity=reviewer.identity,
            reviewer_family=reviewer.family,
            external_receipt_digest=reviewer.external_receipt_digest,
            family_downgraded_reason=downgraded_reason,
            # A caller passing adversarial_pass=true from an unverified posture has
            # it recorded False here — silently in the receipt, but the refusal is
            # visible through family_downgraded_reason and the absent rubric.
            adversarial_pass=bool(review_data.get("adversarial_pass")) and adversarial_verified,
            reviewer_roles_realized=realized_roles,
            prd_ids=prd_ids,
            content_binding=binding_outcome.binding,
            review_plan_id=plan.plan_id,
            review_plan_digest=plan.plan_digest,
            review_input_digest=domain_digest(
                "review_input",
                {
                    "review_plan_id": plan.plan_id,
                    "review_plan_digest": plan.plan_digest,
                    "scope_id": binding_outcome.binding.scope_id,
                    "scope_digest": binding_outcome.binding.scope_digest,
                    "manifest_digest": binding_outcome.binding.manifest_digest,
                    "governing_content_digest": governing_digest,
                },
            ),
            realized_rubric_ids=realized_rubrics,
            verdict=verdict,
            findings=_validated_findings(review_data),
            limitations=str(review_data.get("single_family_caveat", "") or ""),
            degraded_reason=degraded_reason,
            policy_mode=policy_mode,
            config_digest=domain_digest(
                "review_policy_config", {"evidence_receipt_mode": policy_mode, "policy_version": _POLICY_VERSION}
            ),
        )
        outcome = write_receipt(run_path, "review", receipt_id, receipt)
        if not outcome.ok:
            return ReviewReceiptWriteResult(
                plan_id=plan_id,
                state="invalid",
                reason_code=outcome.reason_code,
                reviewer_family=reviewer.family,
                family_downgraded_reason=downgraded_reason,
                verified_cross_model=reviewer.verified_cross_model,
            )
        return ReviewReceiptWriteResult(
            receipt_id=receipt_id,
            plan_id=plan_id,
            state="written",
            reason_code="review_receipt_written",
            reviewer_family=reviewer.family,
            family_downgraded_reason=downgraded_reason,
            verified_cross_model=reviewer.verified_cross_model,
        )
    except Exception as exc:  # justified: review persistence fails toward no evidence, never a false positive
        logger.warning("review_receipt_write_failed", run=str(run_path), error=type(exc).__name__, exc_info=True)
        return ReviewReceiptWriteResult(reason_code="review_receipt_write_failed")


def load_latest_review_evidence(
    run_path: Path | None,
    project_root: Path,
) -> tuple[ReceiptValidationResult, ReviewReceipt | None]:
    """Load and fully validate the newest typed review receipt and its plan."""
    if run_path is None:
        return (
            ReceiptValidationResult(
                state=ReceiptState.LEGACY_UNBOUND,
                reason_code="no_run_pin",
                typed_present=False,
            ),
            None,
        )
    directory = run_path / "meta" / "receipts" / "review"
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not candidates:
        return (
            ReceiptValidationResult(
                state=ReceiptState.LEGACY_UNBOUND,
                reason_code="typed_absent",
                typed_present=False,
            ),
            None,
        )
    path = candidates[0]
    receipt_id = path.stem
    try:
        raw = read_receipt_bytes(run_path, "review", receipt_id)
        if raw is None:
            raise ValueError("receipt unreadable")
        receipt = ReviewReceipt.model_validate_json(raw)
        plan_path = run_path / "meta" / "plans" / "review" / f"{receipt.review_plan_id}.json"
        plan = RequiredReviewPlan.model_validate_json(plan_path.read_bytes())
        return validate_review_receipt(receipt, plan, project_root), receipt
    except Exception:  # justified: present malformed/unreadable typed evidence is non-positive
        logger.warning("review_receipt_load_failed", receipt=receipt_id, run=str(run_path), exc_info=True)
        return (
            ReceiptValidationResult(
                state=ReceiptState.INVALID,
                reason_code="typed_present_invalid",
                receipt_id=receipt_id,
                typed_present=True,
            ),
            None,
        )


__all__ = [
    "ADVERSARIAL_AUDIT_RUBRIC",
    "ReviewReceiptWriteResult",
    "load_latest_review_evidence",
    "record_review_receipt",
]
