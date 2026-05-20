"""AgentWorkEvidence v1 strict schema and validation helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

SCHEMA_VERSION: Literal["agent-work-evidence/v1"] = "agent-work-evidence/v1"


class StrictEvidenceModel(BaseModel):
    """Base class for strict AgentWorkEvidence nested models."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RunIdentity(StrictEvidenceModel):
    """Stable run identity for judge/KG linking."""

    run_id: str = Field(min_length=1)
    run_path: str = Field(min_length=1)


class AgentInfo(StrictEvidenceModel):
    """Agent identity captured from run metadata."""

    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)


class EvidenceTimestamps(StrictEvidenceModel):
    """Deterministic evidence timestamps derived from run artifacts."""

    started_at: str = ""
    generated_at: str = Field(min_length=1)


class ChangedFileEvidence(StrictEvidenceModel):
    """Privacy-safe changed-file metadata."""

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    change_type: Literal["added", "modified", "deleted", "renamed", "copied", "unknown"]
    diff_hash: str | None = Field(default=None, min_length=64, max_length=64)
    related_prds: list[str] = Field(default_factory=list)
    related_frs: list[str] = Field(default_factory=list)


class VerificationEvidence(StrictEvidenceModel):
    """Normalized build/test/static-check evidence."""

    id: str = Field(min_length=1)
    status: Literal["passed", "failed", "missing", "unknown"]
    tests_passed: bool | None = None
    static_checks_clean: bool | None = None
    coverage_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    failure_count: int = Field(default=0, ge=0)
    command: str = ""
    scope: str = ""


class ReviewEvidence(StrictEvidenceModel):
    """Normalized review/self-review evidence item."""

    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: Literal["passed", "warn", "failed", "missing", "unknown"]
    summary: str = ""


class ArtifactEvidence(StrictEvidenceModel):
    """Privacy-safe artifact reference with optional content hash."""

    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)


class EvidenceEventSummary(StrictEvidenceModel):
    """Aggregated event counts reused from report semantics."""

    total_count: int = Field(default=0, ge=0)
    by_type: dict[str, int] = Field(default_factory=dict)


class EvidenceEvent(StrictEvidenceModel):
    """Optional safe event reference; excludes event payload/body."""

    id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    ts: str = ""


class EvidenceIntegrity(StrictEvidenceModel):
    """Integrity hash over canonical evidence JSON excluding this field."""

    algorithm: Literal["sha256"] = "sha256"
    digest: str = Field(min_length=64, max_length=64)


class AgentWorkEvidence(StrictEvidenceModel):
    """Canonical AgentWorkEvidence v1 document."""

    schema_version: Literal["agent-work-evidence/v1"] = SCHEMA_VERSION
    identity: RunIdentity
    task: str = Field(min_length=1)
    prd_scope: list[str] = Field(default_factory=list)
    phase: str = Field(min_length=1)
    status: str = Field(min_length=1)
    agent: AgentInfo
    timestamps: EvidenceTimestamps
    intent: str = ""
    plan_summary: str = ""
    changed_files: list[ChangedFileEvidence] = Field(default_factory=list)
    verification: VerificationEvidence
    review: list[ReviewEvidence] = Field(default_factory=list)
    artifacts: list[ArtifactEvidence] = Field(default_factory=list)
    event_summary: EvidenceEventSummary = Field(default_factory=EvidenceEventSummary)
    events: list[EvidenceEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    integrity: EvidenceIntegrity | None = None


class EvidenceValidationError(StrictEvidenceModel):
    """Machine-readable validation error."""

    loc: list[str]
    type: str
    message: str


class EvidenceValidationResult(StrictEvidenceModel):
    """Result returned by the pure evidence validation helper."""

    valid: bool
    errors: list[EvidenceValidationError] = Field(default_factory=list)


def _canonical_payload(evidence: AgentWorkEvidence) -> str:
    payload = evidence.model_dump(mode="json", exclude={"integrity"}, exclude_none=False)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_agent_work_evidence_integrity(evidence: AgentWorkEvidence) -> EvidenceIntegrity:
    """Compute SHA-256 over canonical JSON excluding the integrity field."""

    digest = hashlib.sha256(_canonical_payload(evidence).encode("utf-8")).hexdigest()
    return EvidenceIntegrity(digest=digest)


def with_agent_work_evidence_integrity(evidence: AgentWorkEvidence) -> AgentWorkEvidence:
    """Return a copy with the deterministic integrity field populated."""

    return evidence.model_copy(update={"integrity": compute_agent_work_evidence_integrity(evidence)})


def validate_agent_work_evidence(data: object) -> EvidenceValidationResult:
    """Validate candidate evidence and return machine-readable errors."""

    try:
        AgentWorkEvidence.model_validate(data)
    except ValidationError as exc:
        errors = [
            EvidenceValidationError(
                loc=[str(part) for part in error["loc"]],
                type=str(error["type"]),
                message=str(error["msg"]),
            )
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        return EvidenceValidationResult(valid=False, errors=errors)
    return EvidenceValidationResult(valid=True, errors=[])
