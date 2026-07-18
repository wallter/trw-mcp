"""Server-resolved review/validation plans — PRD-CORE-205 FR02/FR04.

Belongs to the ``models/evidence_receipts.py`` facade.

A *plan* is the authoritative, server-resolved statement of what a review or
build MUST cover before any evidence is recorded: the governing PRD/spec/rubric
bytes, the authoritative scope, and the complete set of required item IDs. A
receipt then demonstrates *realized* coverage of its plan — a clean verdict or
an arbitrary passing command can never substitute for a missing plan item.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trw_mcp.models._evidence_core import (
    SCHEMA_VERSION,
    EvidenceLimits,
    domain_digest,
)


class CommandClass(str, Enum):
    """Class of an externally executed validation command (FR04)."""

    TEST = "test"
    STATIC = "static"
    BUILD = "build"
    SCHEMA = "schema"
    OTHER = "other"


class ReviewVerdict(str, Enum):
    """Review outcome. ``PASS`` with zero findings is valid (FR02)."""

    PASS = "pass"  # noqa: S105 — enum member, not a secret
    WARN = "warn"
    BLOCK = "block"


class RequiredReviewPlan(BaseModel):
    """Authoritative review plan the server resolves before a review is recorded.

    Binds the exact governing bytes and the complete required rubric/role set.
    ``plan_digest`` is a domain-separated digest of these fields so a receipt
    cannot claim coverage of a different plan revision.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    plan_id: str
    plan_digest: str
    scope_id: str
    scope_digest: str
    governing_prd_ids: tuple[str, ...] = Field(default_factory=tuple)
    governing_content_digest: str = Field(description="Digest of governing PRD/spec/rubric/policy bytes.")
    required_rubric_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_reviewer_roles: tuple[str, ...] = Field(default_factory=tuple)
    policy_version: str = ""

    @model_validator(mode="after")
    def _validate_plan(self) -> RequiredReviewPlan:
        if self.plan_digest != self.expected_digest():
            raise ValueError("review plan_digest does not match canonical plan fields")
        return self

    def expected_digest(self) -> str:
        return domain_digest(
            "review_plan",
            {
                "plan_id": self.plan_id,
                "scope_id": self.scope_id,
                "scope_digest": self.scope_digest,
                "governing_prd_ids": sorted(self.governing_prd_ids),
                "governing_content_digest": self.governing_content_digest,
                "required_rubric_ids": sorted(self.required_rubric_ids),
                "required_reviewer_roles": sorted(self.required_reviewer_roles),
                "policy_version": self.policy_version,
            },
        )


class BuildCommandResult(BaseModel):
    """One externally executed, reporter-asserted command result (FR04).

    Carries only redacted/bounded fields — never raw environment or unredacted
    argv (NFR02). ``command_id`` MUST match an entry in the resolved plan.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    command_id: str
    label: str = Field(description="Safe redacted command label — not the raw argv.")
    command_class: CommandClass
    exit_code: int
    started_at: str = ""
    completed_at: str = ""
    test_count: int | None = Field(default=None, ge=0)
    failure_count: int | None = Field(default=None, ge=0)
    coverage_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    limitations: str = ""

    @field_validator("label", "limitations")
    @classmethod
    def _bound_text(cls, value: str) -> str:
        if len(value.encode("utf-8")) > EvidenceLimits.MAX_FREE_TEXT_BYTES:
            raise ValueError("build command text field exceeds MAX_FREE_TEXT_BYTES")
        return value

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class RequiredValidationPlan(BaseModel):
    """Authoritative build/validation plan resolved before evidence is recorded.

    Binds the complete required command-ID set and thresholds. A build receipt
    passes only with an exact result for every required plan item (FR04).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    plan_id: str
    plan_digest: str
    scope_id: str
    scope_digest: str
    governing_prd_ids: tuple[str, ...] = Field(default_factory=tuple)
    governing_content_digest: str = ""
    policy_config_digest: str = ""
    required_command_ids: tuple[str, ...] = Field(default_factory=tuple)
    optional_command_ids: tuple[str, ...] = Field(default_factory=tuple)
    coverage_threshold: float | None = Field(default=None, ge=0.0, le=100.0)
    policy_version: str = ""

    @model_validator(mode="after")
    def _validate_plan(self) -> RequiredValidationPlan:
        if len(self.required_command_ids) > EvidenceLimits.MAX_PLAN_RESULTS:
            raise ValueError("validation plan exceeds MAX_PLAN_RESULTS required commands")
        if len(set(self.required_command_ids)) != len(self.required_command_ids):
            raise ValueError("validation plan has duplicate required_command_ids")
        if self.plan_digest != self.expected_digest():
            raise ValueError("validation plan_digest does not match canonical plan fields")
        return self

    def expected_digest(self) -> str:
        return domain_digest(
            "validation_plan",
            {
                "plan_id": self.plan_id,
                "scope_id": self.scope_id,
                "scope_digest": self.scope_digest,
                "governing_prd_ids": sorted(self.governing_prd_ids),
                "governing_content_digest": self.governing_content_digest,
                "policy_config_digest": self.policy_config_digest,
                "required_command_ids": sorted(self.required_command_ids),
                "optional_command_ids": sorted(self.optional_command_ids),
                "coverage_threshold": self.coverage_threshold,
                "policy_version": self.policy_version,
            },
        )


class VerificationOutcome(str, Enum):
    """Realized verification outcome (FR06)."""

    PASS = "pass"  # noqa: S105 — enum member, not a secret
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_RUN = "not_run"


ExecutionProvenance = Literal["reporter_asserted"]
