"""Pydantic v2 models for the empirical probe harness (PRD-CORE-144).

Schema pinned here per FR-02 Assertion A1. These types are the typed
contract between :mod:`trw_mcp.probe` (harness / budget / cache / verdict)
and the ``trw_probe`` MCP tools.

``ProbeEvent`` is a payload-backed variant emitted through the unified
``HPOTelemetryEvent`` envelope (PRD-HPO-MEAS-001) — see
:func:`trw_mcp.probe.telemetry.build_probe_event`; this module only defines
the structured ``ProbeResult`` carried inside that event's ``payload``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Verdict = Literal["supports", "refutes", "inconclusive"]


def _utc_now() -> datetime:
    """Return current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


class ResourceBudget(BaseModel):
    """Per-probe resource cap (FR-03). Frozen so cache keys stay stable."""

    model_config = ConfigDict(frozen=True)

    memory_mb: int = Field(default=256, ge=16, le=2048)
    cpu_quota_pct: int = Field(default=100, ge=10, le=400)


class ProbeEvidence(BaseModel):
    """Captured execution evidence (FR-02, FR-03).

    ``wall_ms`` and ``resource_use`` are recorded even on timeout/OOM so the
    rubric and the H4 yield metric see partial evidence rather than nothing.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    wall_ms: int = Field(default=0, ge=0)
    resource_use: dict[str, float] = Field(default_factory=dict)
    encoding_replaced: bool = False
    timed_out: bool = False
    network_attempted: bool = False
    writes_outside_tmp: list[str] = Field(default_factory=list)


class ProbeResult(BaseModel):
    """Typed output of a single probe (FR-02).

    Round-trip JSON serialization is lossless (FR-02 A2); ``confidence``
    outside ``[0,1]`` raises ``ValidationError`` (FR-02 A3).
    """

    hypothesis: str
    hypothesis_id: str | None = None
    verdict: Verdict
    evidence: ProbeEvidence
    confidence: float = Field(ge=0.0, le=1.0)
    ts: datetime = Field(default_factory=_utc_now)
    run_id: str
    budget_override: bool = False
    cache_hit: bool = False


class ProbeAssumption(BaseModel):
    """A plan-branch assumption a probe can be linked to (FR-05).

    ``polarity`` declares the claim direction so contradiction detection
    (FR-06) uses a declared polarity function, not a substring match.
    ``probe_result_ref`` is filled in post-probe by the verdict write-back.
    """

    hypothesis_id: str
    claim: str
    priority: str = "P1"
    polarity: Literal["positive", "negative"] = "positive"
    probe_result_ref: str | None = None


class AssumptionSet(BaseModel):
    """A plan's set of probe-linked assumptions (FR-05 A2).

    Holds the ``assumptions[]`` block a plan branch declares. ``hypothesis_id``
    is the linkage key the harness writes verdicts back to, so it MUST be
    unique within a plan — a duplicate would make verdict write-back ambiguous
    (two assumptions claiming the same id). The validator raises
    ``ValidationError`` on any duplicate (FR-05 Assertion A2).
    """

    model_config = ConfigDict(extra="forbid")

    assumptions: list[ProbeAssumption] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_duplicate_hypothesis_ids(self) -> AssumptionSet:
        """FR-05 A2: a duplicate ``hypothesis_id`` within a plan is invalid."""
        seen: set[str] = set()
        for assumption in self.assumptions:
            hid = assumption.hypothesis_id
            if hid in seen:
                raise ValueError(
                    f"duplicate hypothesis_id {hid!r} in plan assumptions; "
                    "each hypothesis_id must be unique for verdict write-back"
                )
            seen.add(hid)
        return self


class DissentEntry(BaseModel):
    """Dissent Ledger record of a probe-vs-claim contradiction (FR-06)."""

    hypothesis_id: str
    claim: str
    probe_verdict: Verdict
    probe_evidence_ref: str
    ts: datetime = Field(default_factory=_utc_now)


class ProbeBudgetStatus(BaseModel):
    """Read-only budget snapshot returned by ``trw_probe_budget_status`` (FR-10)."""

    used: int = Field(ge=0)
    remaining: int = Field(ge=0)
    total: int = Field(ge=0)
    planning_mode: str
    by_hypothesis_id: dict[str, int] = Field(default_factory=dict)
    by_mode: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "AssumptionSet",
    "DissentEntry",
    "ProbeAssumption",
    "ProbeBudgetStatus",
    "ProbeEvidence",
    "ProbeResult",
    "ResourceBudget",
    "Verdict",
]
