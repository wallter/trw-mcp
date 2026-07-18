"""Typed receipt records — PRD-CORE-205 FR02/FR04/FR06/FR09.

Belongs to the ``models/evidence_receipts.py`` facade.

Each receipt binds its evidence to the current bytes of a run-owned scope and to
a server-resolved plan. ``substantive``/``outcome`` are DERIVED projections of
validated content — never a caller authority. Persistence + current-content
validation live in the service layer; these models own the structural contract
and the coverage/contradiction rules that are pure functions of the payload.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trw_mcp.models._evidence_core import (
    SCHEMA_VERSION,
    ContentBinding,
    EvidenceLimits,
    domain_digest,
)
from trw_mcp.models._evidence_plans import (
    BuildCommandResult,
    ReviewVerdict,
    VerificationOutcome,
)
from trw_mcp.models.run import ReviewFinding


def _require_bounded_text(value: str, field: str) -> str:
    if len(value.encode("utf-8")) > EvidenceLimits.MAX_FREE_TEXT_BYTES:
        raise ValueError(f"{field} exceeds MAX_FREE_TEXT_BYTES")
    return value


class ReviewReceipt(BaseModel):
    """Typed review receipt with zero-finding semantics (FR02).

    ``findings=[]`` with ``verdict=pass`` is valid for a current, completed,
    fully covered review. Substance is DERIVED (:meth:`is_structurally_substantive`
    plus current-content validation in the service) — the ``substantive`` field
    is only a compatibility projection writers emit; readers must not trust it.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    receipt_id: str
    review_id: str
    run_id: str
    completed_at: str
    method: str
    reviewer_origin: str
    reviewer_identity: str
    reviewer_family: str
    reviewer_roles_realized: tuple[str, ...] = Field(default_factory=tuple)
    prd_ids: tuple[str, ...] = Field(default_factory=tuple)
    requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    content_binding: ContentBinding
    review_plan_id: str
    review_plan_digest: str
    review_input_digest: str
    realized_rubric_ids: tuple[str, ...] = Field(default_factory=tuple)
    verdict: ReviewVerdict
    findings: tuple[ReviewFinding, ...] = Field(default_factory=tuple)
    limitations: str = ""
    degraded_reason: str = ""
    policy_mode: str = ""
    config_digest: str = ""
    supersedes_receipt_id: str | None = None

    @field_validator("limitations", "degraded_reason")
    @classmethod
    def _bound_text(cls, value: str) -> str:
        return _require_bounded_text(value, "review text")

    @model_validator(mode="after")
    def _validate_receipt(self) -> ReviewReceipt:
        if len(self.findings) > EvidenceLimits.MAX_FINDINGS:
            raise ValueError("review receipt exceeds MAX_FINDINGS")
        return self

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_reason)

    def covers_plan(self, required_rubric_ids: tuple[str, ...], required_roles: tuple[str, ...]) -> bool:
        """Every required rubric ID and reviewer role has a realized result (FR02)."""
        rubric_ok = set(required_rubric_ids).issubset(set(self.realized_rubric_ids))
        roles_ok = set(required_roles).issubset(set(self.reviewer_roles_realized))
        return rubric_ok and roles_ok

    def is_structurally_substantive(
        self,
        required_rubric_ids: tuple[str, ...],
        required_roles: tuple[str, ...],
    ) -> bool:
        """Pure substance check: schema + coverage + verdict + not degraded.

        Current-content binding is validated separately by the service. A
        ``verdict=block`` receipt is substantive (it recorded a real decision);
        a degraded or plan-incomplete receipt is not.
        """
        if self.is_degraded:
            return False
        if not self.covers_plan(required_rubric_ids, required_roles):
            return False
        return bool(self.completed_at and self.method and self.reviewer_identity)

    def expected_input_digest(self, governing_content_digest: str) -> str:
        """Domain-separated digest of the plan + authoritative scope (FR02).

        ``review_input_digest`` must derive from the server plan and bound scope,
        NOT a caller-supplied list.
        """
        return domain_digest(
            "review_input",
            {
                "review_plan_id": self.review_plan_id,
                "review_plan_digest": self.review_plan_digest,
                "scope_id": self.content_binding.scope_id,
                "scope_digest": self.content_binding.scope_digest,
                "manifest_digest": self.content_binding.manifest_digest,
                "governing_content_digest": governing_content_digest,
            },
        )


class BuildReceipt(BaseModel):
    """Typed build receipt; outcome derived from required command results (FR04)."""

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    receipt_id: str
    run_id: str
    reporter_origin: str = "reporter_asserted"
    completed_at: str
    plan_id: str
    plan_digest: str
    content_binding: ContentBinding
    command_results: tuple[BuildCommandResult, ...] = Field(default_factory=tuple)
    coverage_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    limitations: str = ""
    policy_mode: str = ""
    config_digest: str = ""
    supersedes_receipt_id: str | None = None
    # Legacy compatibility booleans are DERIVED projections; a caller-supplied
    # value that contradicts the derived outcome invalidates the receipt (FR04).
    legacy_tests_passed: bool | None = None
    legacy_static_checks_clean: bool | None = None

    @field_validator("limitations")
    @classmethod
    def _bound_text(cls, value: str) -> str:
        return _require_bounded_text(value, "build limitations")

    @model_validator(mode="after")
    def _validate_receipt(self) -> BuildReceipt:
        if len(self.command_results) > EvidenceLimits.MAX_PLAN_RESULTS:
            raise ValueError("build receipt exceeds MAX_PLAN_RESULTS command results")
        ids = [r.command_id for r in self.command_results]
        if len(set(ids)) != len(ids):
            raise ValueError("build receipt has duplicate command_id results")
        return self

    def covers_required(self, required_command_ids: tuple[str, ...]) -> bool:
        """Exactly one realized result exists for every required plan command."""
        realized = {r.command_id for r in self.command_results}
        return set(required_command_ids).issubset(realized)

    def derived_outcome(self, required_command_ids: tuple[str, ...], coverage_threshold: float | None) -> bool:
        """Pass iff complete required coverage and all required exits are zero (FR04).

        An extra arbitrary successful command never substitutes for a missing
        required command; a below-threshold coverage fails the outcome.
        """
        if not self.covers_required(required_command_ids):
            return False
        by_id = {r.command_id: r for r in self.command_results}
        if not all(by_id[cid].passed for cid in required_command_ids):
            return False
        return not (
            coverage_threshold is not None and (self.coverage_pct is None or self.coverage_pct < coverage_threshold)
        )

    def legacy_contradicts_outcome(self, derived_pass: bool) -> bool:
        """True when a caller legacy boolean contradicts the derived outcome (FR04)."""
        for legacy in (self.legacy_tests_passed, self.legacy_static_checks_clean):
            if legacy is not None and legacy != derived_pass:
                return True
        return False


class VerificationReceipt(BaseModel):
    """Executed verification evidence, distinct from a prospective mapping (FR06).

    Persisting a receipt SHALL NOT mutate PRD/FR status or functionality level —
    that remains an independent review/lifecycle decision. The service never
    writes lifecycle fields; this model is pure execution evidence.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    receipt_id: str
    run_id: str
    requirement_id: str
    mapping_digest: str = Field(description="Digest of the normalized VerificationMapping snapshot.")
    method: str
    executor_origin: str = "reporter_asserted"
    completed_at: str
    content_binding: ContentBinding
    evidence_artifact_path: str = ""
    evidence_artifact_digest: str = ""
    outcome: VerificationOutcome
    observed_values: str = ""
    pass_condition_evaluation: str = ""
    limitations: str = ""
    policy_mode: str = ""
    config_digest: str = ""

    @field_validator("observed_values", "pass_condition_evaluation", "limitations")
    @classmethod
    def _bound_text(cls, value: str) -> str:
        return _require_bounded_text(value, "verification text")

    def matches_mapping(self, current_mapping_digest: str) -> bool:
        """True iff the receipt still names the current mapping revision (FR06).

        A changed mapping does not silently reuse the old receipt — the caller
        reports it against the old ``mapping_digest`` instead.
        """
        return self.mapping_digest == current_mapping_digest


class ReceiptTombstone(BaseModel):
    """Collection tombstone for a garbage-collected receipt (FR09).

    Retained ≥1 year and for the lifetime of any project reference. A tombstoned
    ID SHALL never be reused.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    receipt_type: str
    receipt_id: str
    canonical_digest: str
    collected_at: str
